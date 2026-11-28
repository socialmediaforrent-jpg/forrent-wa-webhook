"""Enlaces de publicaciones en redes sociales, por inmueble.

Tablas aditivas ``crm_redes_links`` (un enlace por inmueble y red) y ``crm_redes_links_log``.
Las redes son las de ``redes.py`` (Instagram, Facebook, TikTok, YouTube, LinkedIn)
más Pinterest en esta barra (sexta casilla). LinkedIn queda en la barra; vacío si no hay publicación.
Nunca se inventan URLs: la casilla queda vacía hasta que hay un enlace de publicación real.
No se usan los perfiles de For Rent de redes.py como si fueran publicaciones del inmueble.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import redes

TZ = ZoneInfo('America/Caracas')
REDES = [r for r in redes.REDES if r['key'] != 'linkedin']
REDES.append({
    'key': 'pinterest', 'nombre': 'Pinterest', 'logo': 'redes/pinterest.svg',
    'dominios': ('pinterest.com',),
})
REDES.extend(r for r in redes.REDES if r['key'] == 'linkedin')
REDES_KEYS = [r['key'] for r in REDES]
POR_KEY = {r['key']: r for r in REDES}

# Solo publicaciones, no el perfil de la inmobiliaria.
_PATH = {
    'instagram': re.compile(r'/(reel|reels|p|tv)/', re.I),
    'facebook': re.compile(r'/(reel|reels|watch|videos|posts|share|permalink\.php|photo|photos|story|stories)/', re.I),
    'tiktok': re.compile(r'/(video|photo)/\d+', re.I),
    'youtube': re.compile(r'/(watch|shorts/|embed/|live/)', re.I),
    'linkedin': re.compile(r'/(posts|feed|pulse|activity|video)/', re.I),
    'pinterest': re.compile(r'/pin/\d+', re.I),
}

# Hosts extra que no están en el perfil de redes.py pero sí son de esa red.
_HOSTS_EXTRA = {
    'facebook': ('fb.watch', 'fb.com'),
    'youtube': ('youtu.be', 'm.youtube.com', 'youtube.com', 'www.youtube.com'),
    'pinterest': ('pinterest.es', 'pinterest.com.mx', 'pinterest.co.uk', 'pinterest.fr',
                  'pinterest.de', 'pinterest.it', 'pinterest.ca', 'pinterest.com.au'),
}


def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    q("""CREATE TABLE IF NOT EXISTS crm_redes_links (
        inmueble_key TEXT NOT NULL, red TEXT NOT NULL, inmueble_codigo TEXT, url TEXT NOT NULL,
        fuente TEXT, creado_por TEXT, created_at TEXT NOT NULL,
        actualizado_por TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY (inmueble_key, red))""")
    q(f"""CREATE TABLE IF NOT EXISTS crm_redes_links_log (
        id {serial}, inmueble_key TEXT NOT NULL, red TEXT NOT NULL, accion TEXT NOT NULL,
        url_antes TEXT, url_despues TEXT, por TEXT, at TEXT NOT NULL)""")


def _hosts(red):
    base = tuple(POR_KEY[red]['dominios']) + _HOSTS_EXTRA.get(red, ())
    out = []
    for h in base:
        h = h.lower()
        if h not in out:
            out.append(h)
    return tuple(out)


def _host_ok(host, dominios):
    host = (host or '').lower()
    return any(host == d or host.endswith('.' + d) for d in dominios)


def url_valida(red, raw):
    """(url_normalizada, error). Solo https de esa red, y de una publicación (no el perfil)."""
    if red not in POR_KEY:
        return None, 'Red no válida.'
    u = (raw or '').strip()
    if not u:
        return None, 'Pega el enlace de la publicación en ' + POR_KEY[red]['nombre'] + '.'
    if len(u) > 500:
        return None, 'El enlace es demasiado largo.'
    try:
        p = urlparse(u)
    except Exception:
        return None, 'El enlace no es válido.'
    host = (p.hostname or '').lower()
    if p.scheme != 'https' or not _host_ok(host, _hosts(red)) or p.username or p.password or p.port:
        return None, 'El enlace debe ser https de una publicación en ' + POR_KEY[red]['nombre'] + '.'
    path = p.path or '/'
    if red == 'youtube' and (host == 'youtu.be' or host.endswith('.youtu.be')):
        vid = path.strip('/')
        if not re.fullmatch(r'[\w-]{6,20}', vid or ''):
            return None, 'El enlace debe ser una publicación de YouTube.'
        return urlunparse(('https', 'www.youtube.com', '/watch', '', 'v=' + vid, '')), None
    if red == 'youtube':
        qs = parse_qs(p.query)
        if path.rstrip('/') == '/watch' and qs.get('v'):
            vid = qs['v'][0]
            if not re.fullmatch(r'[\w-]{6,20}', vid or ''):
                return None, 'El enlace debe ser una publicación de YouTube.'
            return urlunparse(('https', 'www.youtube.com', '/watch', '', urlencode({'v': vid}), '')), None
        if not _PATH['youtube'].search(path):
            return None, 'El enlace debe ser una publicación de YouTube (watch o Shorts), no el canal.'
        return urlunparse(('https', 'www.youtube.com', path, '', '', '')), None
    if not _PATH[red].search(path):
        return None, 'El enlace debe ser la publicación en ' + POR_KEY[red]['nombre'] + ', no el perfil.'
    canon_host = 'www.facebook.com' if red == 'facebook' else host
    if red == 'instagram' and not host.startswith('www.'):
        canon_host = 'www.instagram.com' if host.endswith('instagram.com') else host
    return urlunparse(('https', canon_host, path, '', '', '')), None


def links_por_key(q):
    """{inmueble_key: {red: fila}}."""
    rows = q('SELECT inmueble_key, red, url, fuente, actualizado_por, updated_at FROM crm_redes_links', fetch=True) or []
    out = {}
    for r in rows:
        out.setdefault(r['inmueble_key'], {})[r['red']] = r
    return out


def casillas(links_inmueble):
    """Las 6 casillas (5 de redes.py más Pinterest), con URL solo si hay publicación guardada."""
    links = links_inmueble or {}
    out = []
    for r in REDES:
        row = links.get(r['key'])
        url = (row or {}).get('url') or ''
        out.append({
            **r,
            'url': url,
            'visible': bool(url),
            'fuente': (row or {}).get('fuente') or '',
            'actualizado_por': (row or {}).get('actualizado_por') or '',
            'updated_at': (row or {}).get('updated_at') or '',
        })
    return out


def _log(q, key, red, accion, antes, despues, por):
    q('INSERT INTO crm_redes_links_log (inmueble_key, red, accion, url_antes, url_despues, por, at) '
      'VALUES (%s,%s,%s,%s,%s,%s,%s)', (key, red, accion, antes, despues, por, now_iso()))


def guardar(q, *, key, codigo, red, url, por, fuente=''):
    prev = q('SELECT url FROM crm_redes_links WHERE inmueble_key=%s AND red=%s', (key, red), fetch=True) or []
    ahora = now_iso()
    if prev:
        if prev[0]['url'] == url:
            return 'sin_cambio'
        q('UPDATE crm_redes_links SET url=%s, fuente=%s, actualizado_por=%s, updated_at=%s '
          'WHERE inmueble_key=%s AND red=%s',
          (url, fuente or None, por, ahora, key, red))
        _log(q, key, red, 'reemplazo', prev[0]['url'], url, por)
        return 'reemplazo'
    q('INSERT INTO crm_redes_links (inmueble_key, red, inmueble_codigo, url, fuente, creado_por, created_at, '
      'actualizado_por, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
      (key, red, codigo, url, fuente or None, por, ahora, por, ahora))
    _log(q, key, red, 'alta', None, url, por)
    return 'alta'


def quitar(q, *, key, red, por):
    prev = q('SELECT url FROM crm_redes_links WHERE inmueble_key=%s AND red=%s', (key, red), fetch=True) or []
    if not prev:
        return False
    q('DELETE FROM crm_redes_links WHERE inmueble_key=%s AND red=%s', (key, red))
    _log(q, key, red, 'baja', prev[0]['url'], None, por)
    return True


def historial(q, key, limite=12):
    return q('SELECT red, accion, url_antes, url_despues, por, at FROM crm_redes_links_log WHERE inmueble_key=%s '
             'ORDER BY id DESC LIMIT %s', (key, limite), fetch=True) or []
