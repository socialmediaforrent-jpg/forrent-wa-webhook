"""Mercadeo de portales: enlaces reales de cada inmueble en los 10 portales de For Rent.

Tabla aditiva ``crm_portal_links`` (y su bitácora ``crm_portal_links_log``). No depende de GHL.
Reglas:
- Solo se guardan enlaces reales; nunca se construyen URLs supuestas para un portal.
- Cada URL debe ser https y pertenecer al dominio del portal (evita SSRF y enlaces a otro sitio).
- Un logo solo se muestra si el enlace está verificado: ``ok`` (respondió 2xx) o ``protegido``
  (el portal bloquea robots, p. ej. Cloudflare o la verificación de Mercado Libre, y el enlace
  viene de una fuente verificada del día). ``caido`` = dejó de responder; ``pendiente`` = sin verificar;
  ``ajena`` = ficha con datos de otro captador (no es publicación nuestra): nunca se muestra ni se comparte.
- Solo se muestran publicaciones NUESTRAS (propias y de alianza publicadas con nuestros datos de contacto),
  por eso todas se pueden abrir, copiar y compartir con clientes.
"""
from __future__ import annotations

import re
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Caracas')

# Orden fijo pedido por José (10 casillas). Wasi cuenta como un solo portal.
PORTALES = [
    {'key': 'mercadolibre', 'nombre': 'Mercado Libre', 'logo': 'portales/mercadolibre.png',
     'dominios': ('mercadolibre.com.ve',)},
    {'key': 'rex', 'nombre': 'Rex', 'logo': 'portales/rex.svg', 'dominios': ('rexup.net',)},
    # Wasi: solo nuestro sitio Wasi; las fichas de otras inmobiliarias de la red Wasi no se aceptan.
    {'key': 'wasi', 'nombre': 'Wasi', 'logo': 'portales/wasi.png', 'dominios': ('grupoinmobiliarioforrent.com',)},
    {'key': 'bienesonline', 'nombre': 'Bienes Online', 'logo': 'portales/bienesonline.svg',
     'dominios': ('bienesonline.com', 'bienesonline.ai'),
     # Bienes Online responde 200 con su portada cuando la ficha no existe (soft 404).
     'titulos_genericos': ('propiedades en venezuela',)},
    {'key': 'agentiz', 'nombre': 'Agentiz', 'logo': 'portales/agentiz.png', 'dominios': ('agentiz.com', 'agentiz.net')},
    {'key': 'housy', 'nombre': 'Housy', 'logo': 'portales/housy.png', 'dominios': ('thehousy.com',)},
    {'key': 'anuto', 'nombre': 'Anuto', 'logo': 'portales/anuto.png', 'dominios': ('anuto.app',)},
    {'key': 'propiedash', 'nombre': 'Propiedash', 'logo': 'portales/propiedash.png', 'dominios': ('propiedash.com',)},
    {'key': 'zonaven', 'nombre': 'ZonaVen', 'logo': 'portales/zonaven.svg', 'dominios': ('zonaven.com',)},
    {'key': 'encuadras', 'nombre': 'Encuadras', 'logo': 'portales/encuadras.svg', 'dominios': ('encuadras.com',)},
]
PORTAL_KEYS = [p['key'] for p in PORTALES]
POR_KEY = {p['key']: p for p in PORTALES}
ESTADOS_VISIBLES = ('ok', 'protegido')
ESTADO_ETIQUETA = {
    'ok': 'Verificado', 'protegido': 'Verificado por fuente (el portal bloquea robots)',
    'caido': 'Dejó de responder', 'pendiente': 'Sin verificar',
    'ajena': 'Ficha de otro captador (no es publicación nuestra): no se muestra',
}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/130.0 Safari/537.36')


def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


# ---------------------------------------------------------------- esquema (aditivo)
def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    q(f"""CREATE TABLE IF NOT EXISTS crm_portal_links (
        id {serial},
        inmueble_key TEXT NOT NULL,
        inmueble_codigo TEXT,
        inmueble_match TEXT,
        portal TEXT NOT NULL,
        url TEXT NOT NULL,
        estado TEXT NOT NULL DEFAULT 'pendiente',
        http_status INTEGER,
        verificado_en TEXT,
        ultimo_ok_en TEXT,
        fuente TEXT,
        nota TEXT,
        creado_por TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (inmueble_key, portal))""")
    q("CREATE INDEX IF NOT EXISTS crm_portal_links_portal ON crm_portal_links (portal, estado)")
    q(f"""CREATE TABLE IF NOT EXISTS crm_portal_links_log (
        id {serial}, inmueble_key TEXT NOT NULL, portal TEXT NOT NULL, accion TEXT NOT NULL,
        url_antes TEXT, url_despues TEXT, por TEXT, at TEXT NOT NULL)""")


# ---------------------------------------------------------------- claves de inmueble
def _norm(s):
    s = unicodedata.normalize('NFD', str(s or '')).lower()
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', s).strip()


def _digits(s):
    return re.sub(r'\D', '', str(s or ''))


def codigo_de(it):
    c = str(it.get('codigo') or '').strip()
    return '' if c in ('', '—', '-') else c


def match_de(it):
    """Clave para inmuebles sin código (solo publicados en ML): origen|operación|tipo|zona|precio."""
    return '|'.join([_norm(it.get('origen')), _norm(it.get('operacion')), _norm(it.get('tipo')),
                     _norm(it.get('zona')), _digits(it.get('precio') or it.get('precio_usd'))])


def key_de(it):
    c = codigo_de(it)
    return ('c:' + c) if c else ('m:' + match_de(it))


# ---------------------------------------------------------------- validación de URL
def _host_ok(host, dominios):
    host = (host or '').lower().split(':')[0]
    return any(host == d or host.endswith('.' + d) for d in dominios)


def url_valida(portal, url):
    """(url_limpia, error). Solo https y solo el dominio del portal."""
    p = POR_KEY.get(portal)
    if not p:
        return None, 'Portal no válido.'
    url = (url or '').strip()
    if len(url) > 600 or any(c in url for c in ' \n\r\t"<>\\'):
        return None, 'El enlace no es válido.'
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, 'El enlace no es válido.'
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        return None, 'El enlace debe empezar con https://'
    if not _host_ok(parts.hostname, p['dominios']):
        return None, f"El enlace no es de {p['nombre']} ({', '.join(p['dominios'])})."
    if parts.path in ('', '/') and not parts.query:
        return None, 'Ese es el inicio del portal, no la ficha del inmueble.'
    return url, None


_ANTIBOT_TXT = ('just a moment', 'un momento', 'cf-chl', 'challenge-platform', 'attention required')
_CAIDO_TXT = ('publicación finalizada', 'publicacion finalizada', 'publicación pausada',
              'publicacion pausada', 'esta publicación ya no está disponible')


_NO_ENCONTRADO = ('página no encontrada', 'pagina no encontrada', 'page not found', 'no encontrado', 'error 404')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verificar(portal, url, timeout=12):
    """Devuelve (estado, http_status, detalle). Sigue hasta 5 redirecciones dentro del mismo portal."""
    clean, err = url_valida(portal, url)
    if err:
        return 'caido', None, err
    p = POR_KEY[portal]
    opener = urllib.request.build_opener(_NoRedirect)
    cur = clean
    for _ in range(6):
        req = urllib.request.Request(cur, headers={'User-Agent': UA, 'Accept-Language': 'es-VE,es;q=0.9',
                                                   'Accept': 'text/html,application/xhtml+xml'})
        try:
            r = opener.open(req, timeout=timeout)
            status, headers, body = r.status, r.headers, r.read(250_000)
        except urllib.error.HTTPError as e:
            status, headers = e.code, e.headers
            try:
                body = e.read(60_000)
            except Exception:
                body = b''
        except Exception as e:
            return 'caido', None, type(e).__name__
        txt = body.decode('utf-8', 'ignore').lower() if body else ''
        if status in (301, 302, 303, 307, 308):
            loc = (headers.get('Location') or '').strip()
            nxt = urllib.parse.urljoin(cur, loc)
            if '/gz/account-verification' in nxt or ('/jms/' in nxt and 'login' in nxt):
                return 'protegido', status, 'Mercado Libre pide verificación a robots'
            parts = urlsplit(nxt)
            if parts.scheme != 'https' or not _host_ok(parts.hostname, p['dominios']):
                return 'caido', status, 'Redirige fuera del portal'
            if parts.path in ('', '/') and not parts.query:
                return 'caido', status, 'Redirige al inicio del portal (ficha no existe)'
            cur = nxt
            continue
        if status in (403, 429, 503) and (headers.get('cf-mitigated') or any(t in txt for t in _ANTIBOT_TXT)):
            return 'protegido', status, 'El portal bloquea robots (Cloudflare)'
        if 200 <= status < 300:
            if any(t in txt for t in _CAIDO_TXT):
                return 'caido', status, 'El portal dice que la publicación terminó'
            m = re.search(r'<title[^>]*>([^<]*)', txt)
            titulo = (m.group(1).strip() if m else '')
            if any(t in titulo for t in _NO_ENCONTRADO) or any(titulo.startswith(t) for t in p.get('titulos_genericos', ())):
                return 'caido', status, 'El portal muestra una página genérica o "no encontrada"'
            return 'ok', status, ''
        return 'caido', status, f'HTTP {status}'
    return 'caido', None, 'Demasiadas redirecciones'


# ---------------------------------------------------------------- lectura / escritura
def links_por_key(q):
    rows = q('SELECT * FROM crm_portal_links', fetch=True) or []
    out = {}
    for r in rows:
        out.setdefault(r['inmueble_key'], {})[r['portal']] = r
    return out


def _log(q, key, portal, accion, antes, despues, por):
    q('INSERT INTO crm_portal_links_log (inmueble_key, portal, accion, url_antes, url_despues, por, at) '
      'VALUES (%s,%s,%s,%s,%s,%s,%s)', (key, portal, accion, antes, despues, por, now_iso()))


def guardar(q, *, it, portal, url, estado, http_status=None, fuente='', nota='', por='', verificado_en=None):
    """Inserta o actualiza el enlace (inmueble, portal). ``it`` trae codigo/origen/operacion/tipo/zona/precio."""
    key = key_de(it)
    ahora = now_iso()
    ver = verificado_en or ahora
    prev = q('SELECT id, url, ultimo_ok_en FROM crm_portal_links WHERE inmueble_key=%s AND portal=%s',
             (key, portal), fetch=True) or []
    ok_en = ver if estado in ESTADOS_VISIBLES else ((prev[0].get('ultimo_ok_en') if prev else None))
    if prev:
        q('UPDATE crm_portal_links SET url=%s, estado=%s, http_status=%s, verificado_en=%s, ultimo_ok_en=%s, '
          'fuente=%s, nota=%s, updated_at=%s, inmueble_codigo=%s, inmueble_match=%s WHERE id=%s',
          (url, estado, http_status, ver, ok_en, fuente, nota, ahora, codigo_de(it) or None, match_de(it), prev[0]['id']))
        if prev[0]['url'] != url:
            _log(q, key, portal, 'editar', prev[0]['url'], url, por)
    else:
        q('INSERT INTO crm_portal_links (inmueble_key, inmueble_codigo, inmueble_match, portal, url, estado, http_status, '
          'verificado_en, ultimo_ok_en, fuente, nota, creado_por, created_at, updated_at) '
          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
          (key, codigo_de(it) or None, match_de(it), portal, url, estado, http_status, ver, ok_en,
           fuente, nota, por, ahora, ahora))
        _log(q, key, portal, 'agregar', None, url, por)
    return key


def quitar(q, *, key, portal, por=''):
    prev = q('SELECT url FROM crm_portal_links WHERE inmueble_key=%s AND portal=%s', (key, portal), fetch=True) or []
    if not prev:
        return False
    q('DELETE FROM crm_portal_links WHERE inmueble_key=%s AND portal=%s', (key, portal))
    _log(q, key, portal, 'quitar', prev[0]['url'], None, por)
    return True


def reverificar_fila(q, row):
    estado, status, detalle = verificar(row['portal'], row['url'])
    # Un enlace de fuente verificada que el portal protege de robots no se degrada a "caído".
    ahora = now_iso()
    ok_en = ahora if estado in ESTADOS_VISIBLES else row.get('ultimo_ok_en')
    q('UPDATE crm_portal_links SET estado=%s, http_status=%s, verificado_en=%s, ultimo_ok_en=%s, nota=%s WHERE id=%s',
      (estado, status, ahora, ok_en, detalle or None, row['id']))
    return estado


_job = {'corriendo': False, 'inicio': None, 'fin': None, 'total': 0, 'hechos': 0, 'resumen': {}}
_job_lock = threading.Lock()


def estado_job():
    with _job_lock:
        return dict(_job)


def reverificar_todo(q, *, en_segundo_plano=True, hilos=6):
    with _job_lock:
        if _job['corriendo']:
            return False
        rows = q("SELECT * FROM crm_portal_links WHERE estado <> 'ajena'", fetch=True) or []
        _job.update(corriendo=True, inicio=now_iso(), fin=None, total=len(rows), hechos=0, resumen={})

    def run():
        resumen = {}
        try:
            with ThreadPoolExecutor(max_workers=hilos) as ex:
                for est in ex.map(lambda r: _safe(q, r), rows):
                    resumen[est] = resumen.get(est, 0) + 1
                    with _job_lock:
                        _job['hechos'] += 1
        finally:
            with _job_lock:
                _job.update(corriendo=False, fin=now_iso(), resumen=resumen)

    if en_segundo_plano:
        threading.Thread(target=run, daemon=True, name='portal-links-verify').start()
    else:
        run()
    return True


def _safe(q, r):
    try:
        return reverificar_fila(q, r)
    except Exception:
        return 'error'


# ---------------------------------------------------------------- armado de la vista
def casillas(links_inmueble):
    out = []
    for p in PORTALES:
        r = (links_inmueble or {}).get(p['key'])
        estado = (r or {}).get('estado') or ''
        out.append({
            'key': p['key'], 'nombre': p['nombre'], 'logo': p['logo'],
            'url': (r or {}).get('url') or '',
            'estado': estado,
            'visible': bool(r) and estado in ESTADOS_VISIBLES,
            'caido': bool(r) and estado == 'caido',
            'pendiente': bool(r) and estado == 'pendiente',
            'ajena': bool(r) and estado == 'ajena',
            'etiqueta': ESTADO_ETIQUETA.get(estado, ''),
            'verificado_en': (r or {}).get('verificado_en') or '',
            'fuente': (r or {}).get('fuente') or '',
            'nota': (r or {}).get('nota') or '',
        })
    return out
