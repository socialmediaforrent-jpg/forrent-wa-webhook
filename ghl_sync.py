"""Sincronización periódica de conversaciones GHL como fuente de la bandeja Mensajes.

Trae WhatsApp, Instagram, Facebook, email y SMS desde /conversations/search y los
mensajes de cada conversación actualizada. Deduplica por id de GHL (wamid='ghl:{id}')
y por altId de Meta, para no repetir lo que ya llegó por el webhook.

No envía mensajes, no borra filas y nunca registra tokens. El hilo de fondo no se
arranca en pytest ni en el ciclo de un request (el pull-to-refresh solo avisa al
hilo). Intervalo: GHL_SYNC_INTERVAL_SEC (por defecto 45). Cada pasada tiene un
tope de GHL_SYNC_MAX_SEC (8 s) y las llamadas HTTP un timeout de 8 s. Se apaga
con GHL_SYNC_DISABLED=1.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger('ghl_sync')

GHL_API_BASE = 'https://services.leadconnectorhq.com'
GHL_API_VERSION = '2021-07-28'
CORTE_ISO = '2026-10-31T00:00:00+00:00'
INTERVALO_DEF = 45
# Número de WhatsApp de For Rent: no usarlo como wa_id del cliente.
NUMEROS_PROPIOS = {'584242294196', '4242294196'}

KEEP_TYPES = {
    'TYPE_WHATSAPP', 'TYPE_INSTAGRAM', 'TYPE_FACEBOOK',
    'TYPE_INSTAGRAM_COMMENT', 'TYPE_FACEBOOK_COMMENT',
    'TYPE_EMAIL', 'TYPE_SMS', 'TYPE_GMB',
}
CANAL_DE_TIPO = {
    'TYPE_WHATSAPP': 'whatsapp',
    'TYPE_INSTAGRAM': 'instagram',
    'TYPE_INSTAGRAM_COMMENT': 'instagram',
    'TYPE_FACEBOOK': 'facebook',
    'TYPE_FACEBOOK_COMMENT': 'facebook',
    'TYPE_EMAIL': 'email',
    'TYPE_SMS': 'sms',
}
TIPO_ENVIO = {
    'whatsapp': 'WhatsApp',
    'instagram': 'IG',
    'facebook': 'FB',
    'email': 'Email',
    'sms': 'SMS',
}

_lock = threading.Lock()
_hilo = None
_parar = threading.Event()
_pedir = threading.Event()
_ultima = 0.0
_ultimo_resumen = {}


def configurado():
    return bool((os.environ.get('GHL_API_TOKEN') or '').strip() and
                (os.environ.get('GHL_LOCATION_ID') or '').strip())


def intervalo():
    try:
        n = int(os.environ.get('GHL_SYNC_INTERVAL_SEC') or INTERVALO_DEF)
    except ValueError:
        n = INTERVALO_DEF
    return max(15, min(n, 600))


def timeout_http():
    try:
        n = float(os.environ.get('GHL_SYNC_HTTP_TIMEOUT_SEC') or 8)
    except ValueError:
        n = 8
    return max(2.0, min(n, 15.0))


def max_ciclo():
    try:
        n = float(os.environ.get('GHL_SYNC_MAX_SEC') or 8)
    except ValueError:
        n = 8
    return max(3.0, min(n, 25.0))


def retraso_inicial():
    try:
        n = float(os.environ.get('GHL_SYNC_START_DELAY_SEC') or 15)
    except ValueError:
        n = 15
    return max(0.0, min(n, 60.0))


def ensure_tables(q):
    q("""CREATE TABLE IF NOT EXISTS ghl_sync_state (
            clave TEXT PRIMARY KEY,
            valor TEXT,
            at TEXT
        )""")


def _ahora_iso():
    return datetime.now(timezone.utc).isoformat()


def _ms(ts):
    """Convierte un ts GHL (ms) o ISO a milisegundos UTC, o 0."""
    if ts is None or ts == '':
        return 0
    if isinstance(ts, (int, float)):
        n = int(ts)
        return n if n > 10**12 else n * 1000
    s = str(ts).strip()
    if s.isdigit():
        n = int(s)
        return n if n > 10**12 else n * 1000
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _iso_de_ms(ms):
    if not ms:
        return ''
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def corte_ms():
    return _ms(CORTE_ISO)


def canal_de(msg, conv=None):
    t = (msg or {}).get('messageType') or ''
    if t in CANAL_DE_TIPO:
        return CANAL_DE_TIPO[t]
    t2 = (conv or {}).get('lastMessageType') or ''
    return CANAL_DE_TIPO.get(t2, 'whatsapp')


def norm_phone(s):
    d = ''.join(c for c in str(s or '') if c.isdigit())
    if d.startswith('00'):
        d = d[2:]
    if d.startswith('580') and len(d) >= 12:
        d = '58' + d[3:]
    elif d.startswith('0') and len(d) >= 11:
        d = '58' + d.lstrip('0')
    elif len(d) == 10 and d.startswith('4'):
        d = '58' + d
    return d


def _es_telefono(s):
    n = norm_phone(s)
    return n if len(n) >= 10 else ''


def telefono_cliente(msg, propios=None):
    """Teléfono del cliente (no el de For Rent) a partir de from/to."""
    propios = set(propios or ()) | NUMEROS_PROPIOS
    direccion = ((msg or {}).get('direction') or '').lower()
    origen = _es_telefono((msg or {}).get('from'))
    destino = _es_telefono((msg or {}).get('to'))
    candidatos = []
    if direccion == 'inbound':
        candidatos = [origen, destino]
    else:
        candidatos = [destino, origen]
    for n in candidatos:
        if n and n not in propios and not any(n.endswith(p[-10:]) for p in propios if len(p) >= 10):
            return n
    for n in (origen, destino):
        if n:
            return n
    return ''


def _email_de(msg, conv):
    for s in ((msg or {}).get('from'), (msg or {}).get('to'),
              (conv or {}).get('contactName'), (conv or {}).get('fullName'),
              (conv or {}).get('email')):
        t = (s or '').strip()
        if t and '@' in t and ' ' not in t:
            return t.lower()
    return ''


def thread_id(canal, msg, conv=None, propios=None):
    """Clave de la fila en la bandeja. WhatsApp usa el teléfono para unirse al webhook de Meta."""
    cid = str((msg or {}).get('contactId') or (conv or {}).get('contactId') or '').strip()
    if canal == 'whatsapp':
        return telefono_cliente(msg, propios=propios) or cid
    if canal == 'instagram':
        return 'ig:' + cid if cid else ''
    if canal == 'facebook':
        return 'fb:' + cid if cid else ''
    if canal == 'email':
        return 'email:' + (_email_de(msg, conv) or cid)
    if canal == 'sms':
        tel = telefono_cliente(msg, propios=propios)
        return 'sms:' + (tel or cid)
    return cid


def tipo_envio(canal):
    return TIPO_ENVIO.get(canal or 'whatsapp', 'WhatsApp')


def _texto(msg):
    body = ((msg or {}).get('body') or '').strip()
    atts = (msg or {}).get('attachments') or []
    extras = []
    for a in atts:
        url = a if isinstance(a, str) else (a.get('url') or '')
        if url:
            extras.append(url)
    if not body and extras:
        return extras[0]
    if extras:
        return (body + ' ' + ' '.join(extras)).strip()
    return body


def _msg_type(msg):
    atts = (msg or {}).get('attachments') or []
    if not atts:
        return 'text'
    url = atts[0] if isinstance(atts[0], str) else (atts[0].get('url') or '')
    u = url.split('?')[0].lower()
    if u.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
        return 'image'
    if u.endswith(('.mp4', '.mov', '.3gp')):
        return 'video'
    if u.endswith(('.ogg', '.oga', '.opus', '.mp3', '.m4a', '.aac', '.wav', '.amr')):
        return 'audio'
    return 'document'


def fila_de_mensaje(msg, conv=None, propios=None):
    """Fila lista para wa_events, o None si se omite (sin id, tipo interno, etc.)."""
    mid = str((msg or {}).get('id') or '').strip()
    if not mid:
        return None
    tipo = (msg or {}).get('messageType') or ''
    if tipo and tipo not in KEEP_TYPES:
        return None
    if (msg or {}).get('isInternal') or (conv or {}).get('isLastMessageInternalComment') and tipo == 'TYPE_INTERNAL':
        return None
    canal = canal_de(msg, conv)
    wa = thread_id(canal, msg, conv, propios=propios)
    if not wa:
        return None
    direction = 'inbound' if ((msg or {}).get('direction') or '').lower() == 'inbound' else 'outbound'
    ts = (msg or {}).get('dateAdded') or ''
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and str(ts).isdigit()):
        ts = _iso_de_ms(_ms(ts))
    nombre = ((conv or {}).get('contactName') or (conv or {}).get('fullName') or
              (msg or {}).get('name') or '')
    alt = str((msg or {}).get('altId') or '').strip()
    return {
        'kind': 'message' if direction == 'inbound' else 'outbound',
        'wamid': 'ghl:' + mid,
        'wa_id': wa,
        'name': nombre,
        'direction': direction,
        'msg_type': _msg_type(msg),
        'text': _texto(msg),
        'ts': ts,
        'status': (msg or {}).get('status') or '',
        'auto': 0,
        'source': 'ghl_sync',
        'phone_number_id': '',
        'received_at': ts or _ahora_iso(),
        'data': {
            'ghl_message_id': mid,
            'ghl_conversation_id': (msg or {}).get('conversationId') or (conv or {}).get('id'),
            'ghl_contact_id': (msg or {}).get('contactId') or (conv or {}).get('contactId'),
            'channel': canal,
            'canal': canal,
            'messageType': tipo,
            'meta_wamid': alt,
            'attachments': (msg or {}).get('attachments') or [],
        },
    }


def _token():
    return (os.environ.get('GHL_API_TOKEN') or '').strip()


def _location():
    return (os.environ.get('GHL_LOCATION_ID') or '').strip()


def _headers():
    return {
        'Authorization': 'Bearer ' + _token(),
        'Version': GHL_API_VERSION,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'ForRentOnline/1.0',
    }


def llamar(method, path, *, params=None, body=None, opener=None, timeout=None):
    """(status, dict_o_texto). Nunca incluye el token en el valor devuelto."""
    if timeout is None:
        timeout = timeout_http()
    url = GHL_API_BASE + path
    if params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None and v != ''})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    try:
        with open_fn(req, timeout=timeout) as r:
            raw = r.read() or b'{}'
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode(errors='replace')[:400]
        except Exception:
            raw = ''
        return e.code, raw
    except Exception as e:
        return 0, type(e).__name__


def _retry(method, path, **kw):
    delay = 0.4
    st, d = 0, ''
    for _ in range(3):
        st, d = llamar(method, path, **kw)
        if st == 429 or st >= 500 or st == 0:
            time.sleep(delay)
            delay = min(delay * 2, 2.0)
            continue
        return st, d
    return st, d


def listar_conversaciones(desde_ms, *, loc=None, opener=None, limite=20, max_paginas=15):
    """Conversaciones con lastMessageDate >= desde_ms, más recientes primero."""
    loc = loc or _location()
    out, cursor = [], None
    for _ in range(max_paginas):
        params = {'locationId': loc, 'limit': limite, 'sortBy': 'last_message_date', 'sort': 'desc'}
        if cursor:
            params['startAfterDate'] = str(cursor)
        st, d = _retry('GET', '/conversations/search', params=params, opener=opener)
        if st != 200 or not isinstance(d, dict):
            return out, f'search {st}'
        batch = d.get('conversations') or []
        if not batch:
            break
        stop = False
        for c in batch:
            ms = _ms(c.get('lastMessageDate') or c.get('sort') or 0)
            if desde_ms and ms and ms < desde_ms:
                stop = True
                break
            out.append(c)
        if stop or len(batch) < limite:
            break
        last_ms = _ms(batch[-1].get('lastMessageDate') or 0)
        if not last_ms or last_ms == cursor:
            break
        cursor = last_ms
    return out, None


def mensajes_de(conv_id, *, opener=None, ya_conocidos=None, max_paginas=8):
    """Página de mensajes más recientes primero (o según API). Para al llegar a ids ya guardados."""
    ya = set(ya_conocidos or ())
    msgs, last, err = [], None, None
    for _ in range(max_paginas):
        params = {'limit': 100}
        if last:
            params['lastMessageId'] = last
        st, d = _retry('GET', f'/conversations/{conv_id}/messages', params=params, opener=opener)
        if st != 200 or not isinstance(d, dict):
            err = f'messages {st}'
            break
        mm = d.get('messages') or {}
        chunk = mm.get('messages') if isinstance(mm, dict) else (mm or [])
        if not chunk:
            break
        msgs.extend(chunk)
        if ya and any(('ghl:' + str(m.get('id') or '')) in ya for m in chunk):
            break
        if isinstance(mm, dict):
            if not mm.get('nextPage'):
                break
            last = mm.get('lastMessageId') or chunk[-1].get('id')
        else:
            break
    # únicos, viejos primero
    seen, out = set(), []
    for m in msgs:
        mid = m.get('id')
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    out.sort(key=lambda m: m.get('dateAdded') or '')
    return out, err


def _cargar_estado(q):
    ensure_tables(q)
    rows = q("SELECT clave, valor FROM ghl_sync_state", fetch=True) or []
    return {r['clave']: r['valor'] for r in rows}


def _guardar_estado(q, datos):
    ensure_tables(q)
    at = _ahora_iso()
    for k, v in datos.items():
        ya = q("SELECT 1 AS x FROM ghl_sync_state WHERE clave=%s", (k,), fetch=True)
        if ya:
            q("UPDATE ghl_sync_state SET valor=%s, at=%s WHERE clave=%s", (str(v), at, k))
        else:
            q("INSERT INTO ghl_sync_state (clave, valor, at) VALUES (%s,%s,%s)", (k, str(v), at))


def _meta_sql(is_pg):
    if is_pg:
        return "(data->>'meta_wamid')"
    return "json_extract(data, '$.meta_wamid')"


def ids_ya_guardados(q, is_pg=False, alts=None, ghl_ids=None):
    """Conjunto de wamid ya presentes (ghl:…, wamid de Meta y data.meta_wamid)."""
    conocidos = set()
    ids = [x for x in (ghl_ids or []) if x]
    alts = [x for x in (alts or []) if x]
    if not ids and not alts:
        return conocidos
    meta = _meta_sql(is_pg)
    # Consultas cortas, por lotes.
    for lote in _lotes(ids, 200):
        ph = ','.join(['%s'] * len(lote))
        wamids = ['ghl:' + i for i in lote]
        rows = q(f"SELECT wamid FROM wa_events WHERE wamid IN ({ph}) AND kind IN ('message','outbound')",
                 tuple(wamids), fetch=True) or []
        for r in rows:
            if r.get('wamid'):
                conocidos.add(r['wamid'])
    for lote in _lotes(alts, 200):
        ph = ','.join(['%s'] * len(lote))
        rows = q(
            f"""SELECT wamid, {meta} AS meta_wamid FROM wa_events
                WHERE kind IN ('message','outbound')
                  AND (wamid IN ({ph}) OR {meta} IN ({ph}))""",
            tuple(lote) + tuple(lote), fetch=True,
        ) or []
        for r in rows:
            if r.get('wamid'):
                conocidos.add(r['wamid'])
            if r.get('meta_wamid'):
                conocidos.add(r['meta_wamid'])
    return conocidos


def _lotes(xs, n):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def ya_existe(fila, conocidos):
    """True si esta fila de GHL ya está (por id GHL o por altId de Meta)."""
    w = fila.get('wamid') or ''
    alt = ((fila.get('data') or {}) if isinstance(fila.get('data'), dict) else {}).get('meta_wamid') or ''
    if w and w in conocidos:
        return True
    if alt and alt in conocidos:
        return True
    return False


def aplicar_filas(filas, *, insert_event, conocidos_iniciales=None):
    """Inserta filas nuevas. Devuelve (insertadas, omitidas). No borra nada."""
    conocidos = set(conocidos_iniciales or ())
    ins = omit = 0
    for fila in filas:
        w = fila.get('wamid') or ''
        alt = (fila.get('data') or {}).get('meta_wamid') or ''
        if ya_existe(fila, conocidos):
            omit += 1
            continue
        insert_event(**fila)
        ins += 1
        if w:
            conocidos.add(w)
        if alt:
            conocidos.add(alt)
        mid = (fila.get('data') or {}).get('ghl_message_id')
        if mid:
            conocidos.add('ghl:' + str(mid))
    return ins, omit


def cursor_inicial(q, is_pg=False):
    """Desde cuándo pedir: último ts guardado (solape 5 min) o el corte del 31-oct."""
    rows = q(
        """SELECT MAX(ts) AS t FROM wa_events
           WHERE kind IN ('message','outbound') AND ts IS NOT NULL AND ts <> ''""",
        fetch=True,
    ) or []
    t = (rows[0] or {}).get('t') if rows else None
    ms = _ms(t) if t else 0
    base = max(ms - 5 * 60 * 1000, corte_ms()) if ms else corte_ms()
    return base


def sincronizar(q, insert_event, *, is_pg=False, opener=None, loc=None, on_change=None):
    """Una pasada de sincronización. Devuelve un resumen sin secretos ni textos."""
    resumen = {
        'ok': False, 'at': _ahora_iso(), 'conversaciones': 0, 'insertadas': 0,
        'omitidas': 0, 'errores': 0, 'error': '', 'cortado': False,
    }
    if not _lock.acquire(blocking=False):
        resumen['error'] = 'ocupado'
        return resumen
    try:
        return _sincronizar_cuerpo(q, insert_event, is_pg=is_pg, opener=opener,
                                  loc=loc, on_change=on_change, resumen=resumen)
    finally:
        _lock.release()


def _sincronizar_cuerpo(q, insert_event, *, is_pg, opener, loc, on_change, resumen):
    if not configurado() and opener is None:
        # En pruebas se pasa opener; en vivo hacen falta las variables.
        if not (os.environ.get('GHL_API_TOKEN') and os.environ.get('GHL_LOCATION_ID')):
            resumen['error'] = 'no_configurado'
            return resumen
    loc = loc or _location()
    estado = _cargar_estado(q)
    try:
        desde = int(estado.get('cursor_ms') or 0)
    except ValueError:
        desde = 0
    if desde < corte_ms():
        desde = cursor_inicial(q, is_pg=is_pg)
    convs, err = listar_conversaciones(desde, loc=loc, opener=opener)
    if err and not convs:
        resumen['error'] = err
        _guardar_estado(q, {'last_error': err, 'last_run_at': resumen['at']})
        return resumen
    resumen['conversaciones'] = len(convs)
    max_ms = desde
    limite = time.monotonic() + max_ciclo()
    for c in convs:
        if time.monotonic() >= limite:
            resumen['cortado'] = True
            break
        last = _ms(c.get('lastMessageDate') or 0)
        if c.get('isLastMessageInternalComment') and not (c.get('lastMessageType') in KEEP_TYPES):
            if last:
                max_ms = max(max_ms, last)
            continue
        if last and last < corte_ms():
            max_ms = max(max_ms, last)
            continue
        msgs, e2 = mensajes_de(c.get('id'), opener=opener)
        if e2:
            resumen['errores'] += 1
            if not resumen['error']:
                resumen['error'] = e2
        filas = []
        ghl_ids, alts = [], []
        for m in msgs:
            fila = fila_de_mensaje(m, c)
            if not fila:
                continue
            filas.append(fila)
            ghl_ids.append((fila.get('data') or {}).get('ghl_message_id'))
            alt = (fila.get('data') or {}).get('meta_wamid')
            if alt:
                alts.append(alt)
        conocidos = ids_ya_guardados(q, is_pg=is_pg, alts=alts, ghl_ids=ghl_ids)
        ins, omit = aplicar_filas(filas, insert_event=insert_event, conocidos_iniciales=conocidos)
        resumen['insertadas'] += ins
        resumen['omitidas'] += omit
        if last:
            max_ms = max(max_ms, last)
    resumen['ok'] = True
    _guardar_estado(q, {
        'cursor_ms': str(max_ms or desde),
        'last_run_at': resumen['at'],
        'last_error': resumen['error'] or '',
        'last_inserted': str(resumen['insertadas']),
    })
    if resumen['insertadas'] and callable(on_change):
        try:
            on_change()
        except Exception:
            log.exception('on_change de ghl_sync falló')
    log.info('ghl_sync ok convos=%s insertadas=%s omitidas=%s errores=%s',
             resumen['conversaciones'], resumen['insertadas'], resumen['omitidas'], resumen['errores'])
    return resumen


def existe_meta_ya(q, wamid, is_pg=False):
    """True si ese wamid de Meta ya está como fila o como data.meta_wamid (sync GHL)."""
    if not wamid:
        return False
    meta = _meta_sql(is_pg)
    rows = q(
        f"""SELECT 1 AS x FROM wa_events
            WHERE kind IN ('message','outbound')
              AND (wamid=%s OR {meta}=%s)
            LIMIT 1""",
        (wamid, wamid), fetch=True,
    )
    return bool(rows)


def resumen_ultimo():
    return dict(_ultimo_resumen)


def en_pruebas():
    return ('pytest' in sys.modules) or bool(os.environ.get('PYTEST_CURRENT_TEST')) or os.environ.get('GHL_SYNC_DISABLED') == '1'


def iniciar_fondo(app_factory, *, insert_event, is_pg, on_change=None):
    """Arranca un hilo daemon. app_factory() debe devolver (q,). No se usa en pytest.

    Espera retraso_inicial() antes de la primera pasada para no competir con /healthz
    al arrancar el worker. No corre dos pasadas seguidas por la carrera de _pedir.
    """
    global _hilo
    if en_pruebas() or not configurado():
        return False
    if _hilo and _hilo.is_alive():
        return True

    def loop():
        global _ultima, _ultimo_resumen
        _parar.wait(retraso_inicial())
        while not _parar.is_set():
            disparo = _pedir.is_set()
            _pedir.clear()
            if not disparo and (time.time() - _ultima) < intervalo() and _ultima:
                _parar.wait(1)
                continue
            try:
                q, = app_factory()
                _ultimo_resumen = sincronizar(q, insert_event, is_pg=is_pg, on_change=on_change) or {}
            except Exception:
                log.exception('ghl_sync fondo')
            _ultima = time.time()
            _pedir.clear()
            _parar.wait(1)

    _hilo = threading.Thread(target=loop, name='ghl-sync', daemon=True)
    _hilo.start()
    return True


def avisar():
    """Pide una pasada al hilo (no bloquea). Si no hay hilo, no hace nada."""
    if en_pruebas():
        return
    _pedir.set()
