"""Bandeja unificada: responder desde el portal (WhatsApp por GHL o Cloud API) y canales IG/FB (solo lectura).

Reglas que se aplican en el SERVIDOR (no solo en la pantalla):
- Solo se responde: nunca se inicia una conversación ni se usan plantillas.
- Texto libre solo dentro de las 24 h desde el último mensaje del cliente (ventana de servicio).
- Mismo filtro que los scripts de la caja (send_reply / wa_send): sin enlaces wa.me ni teléfonos de asesores.
- WhatsApp envía si está configurado GHL (GHL_API_TOKEN y GHL_LOCATION_ID) o Meta (META_WA_TOKEN y
  WA_PHONE_NUMBER_ID). Sin ellos el botón queda deshabilitado con la explicación. WA_SEND_PROVIDER=ghl
  fuerza GHL; si no se indica, se usa GHL cuando no hay credenciales de Meta.
- El envío por GHL busca el contactId por teléfono (no crea el contacto si GHL no lo tiene).
- Instagram / Facebook: lectura por Graph API solo si META_SOCIAL_INBOX=1 y hay token de página
  (META_PAGE_TOKEN o el archivo META_PAGE_TOKEN_FILE). Solo admin los ve (todavía no hay forma segura de
  vincular un chat de IG/FB con el cliente asignado de un asesor). Responder IG/FB queda deshabilitado
  hasta que Meta apruebe la revisión de la app.
Nunca se registran ni se devuelven tokens.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

VENTANA = timedelta(hours=24)
MAX_TEXTO = 4096
ENVIOS_POR_MINUTO = 20

META_PAGE_ID_DEF = '552585004598339'
META_IG_ID_DEF = '17841455210156889'


def _graph_version():
    return os.environ.get('GRAPH_VERSION') or 'v27.0'


# ------------------------------------------------------------------ WhatsApp: envío
GHL_API_BASE = 'https://services.leadconnectorhq.com'
GHL_API_VERSION = '2021-07-28'


def wa_meta_configurado():
    return bool(os.environ.get('META_WA_TOKEN') and os.environ.get('WA_PHONE_NUMBER_ID'))


def ghl_envio_configurado():
    return bool((os.environ.get('GHL_API_TOKEN') or '').strip() and (os.environ.get('GHL_LOCATION_ID') or '').strip())


def wa_envio_configurado():
    """True si hay algún proveedor de envío listo (GHL o Meta)."""
    return ghl_envio_configurado() or wa_meta_configurado()


def proveedor_envio():
    """Proveedor a usar: 'ghl' o 'meta'. WA_SEND_PROVIDER lo fuerza; si falta, GHL cuando no hay Meta."""
    p = (os.environ.get('WA_SEND_PROVIDER') or '').strip().lower()
    if p == 'ghl':
        return 'ghl'
    if p == 'meta':
        return 'meta'
    return 'meta' if wa_meta_configurado() else 'ghl'


def _parse(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def ventana(ultimo_entrante_iso, now=None):
    """(abierta, vence_dt, motivo). Sin mensaje entrante -> cerrada (nunca iniciamos)."""
    now = now or datetime.now(timezone.utc)
    dt = _parse(ultimo_entrante_iso)
    if not dt:
        return False, None, 'Este contacto nunca nos escribió por WhatsApp. No iniciamos conversaciones.'
    vence = dt + VENTANA
    if now >= vence:
        return False, vence, ('Pasaron más de 24 horas desde su último mensaje. Meta solo permite plantillas '
                              'y no usamos plantillas: hay que esperar a que el cliente vuelva a escribir.')
    return True, vence, ''


def estado_respuesta(canal, ultimo_entrante_iso, now=None):
    """Lo que la pantalla necesita para pintar la caja de respuesta (sin secretos)."""
    if canal in ('instagram', 'facebook'):
        return {'canal': canal, 'puede_enviar': False, 'vence': '',
                'motivo': 'Responder por Instagram/Facebook desde el portal se activa cuando Meta apruebe la '
                          'revisión de la app. Por ahora se responde desde GHL.'}
    abierta, vence, motivo = ventana(ultimo_entrante_iso, now)
    if abierta and not wa_envio_configurado():
        return {'canal': 'whatsapp', 'puede_enviar': False, 'vence': vence.isoformat() if vence else '',
                'motivo': 'El envío por WhatsApp desde el portal aún no está activado (falta conectar el envío en el servidor).'}
    return {'canal': 'whatsapp', 'puede_enviar': abierta, 'vence': vence.isoformat() if vence else '',
            'motivo': motivo}


_WA_ME = re.compile(r'(wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com)', re.I)


def _digitos(s):
    return ''.join(c for c in str(s or '') if c.isdigit())


def revisar_texto(texto, telefonos_bloqueados=()):
    """Devuelve el texto limpio o lanza ValueError con el motivo (en español)."""
    t = (texto or '').replace('\r\n', '\n').strip('\n')
    if not t.strip():
        raise ValueError('El mensaje está vacío.')
    if len(t) > MAX_TEXTO:
        raise ValueError(f'El mensaje pasa de {MAX_TEXTO} caracteres (límite de WhatsApp).')
    if _WA_ME.search(t):
        raise ValueError('No se envían enlaces wa.me ni de WhatsApp.')
    dig = _digitos(t)
    for tel in telefonos_bloqueados or ():
        d = _digitos(tel)
        if len(d) < 10:
            continue
        local = d[-10:]  # 4121234567 sin 58 ni 0
        if local in dig:
            raise ValueError('El mensaje incluye el teléfono de un asesor. No se comparte con clientes.')
    return t


class EnvioError(Exception):
    def __init__(self, msg, codigo=None):
        super().__init__(msg)
        self.codigo = codigo


_META_ERRORES = {
    131047: 'Meta rechazó el mensaje: pasaron más de 24 horas desde el último mensaje del cliente.',
    131026: 'Meta no pudo entregar el mensaje a ese número.',
    131056: 'Demasiados mensajes seguidos a este número. Espera un momento.',
    130429: 'Meta limitó los envíos por ahora. Intenta en unos minutos.',
    190: 'El token de WhatsApp del servidor no es válido o venció.',
}


def _ghl_token():
    return (os.environ.get('GHL_API_TOKEN') or '').strip()


def _ghl_location():
    return (os.environ.get('GHL_LOCATION_ID') or '').strip()


def _ghl_headers():
    return {'Authorization': 'Bearer ' + _ghl_token(), 'Version': GHL_API_VERSION,
            'Accept': 'application/json', 'Content-Type': 'application/json',
            'User-Agent': 'ForRentOnline/1.0'}


def _ghl_call(method, path, *, params=None, body=None, opener=None):
    """Llama a la API de GHL. Devuelve (status, dict_o_none). Nunca expone el token."""
    url = GHL_API_BASE + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_ghl_headers())
    op = opener or urllib.request.urlopen
    try:
        with op(req, timeout=20) as r:
            raw = r.read() or b'{}'
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {}
            return getattr(r, 'status', 200) or 200, parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as e:
        raw = b''
        try:
            raw = e.read() or b''
        except Exception:
            raw = b''
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
        return e.code, parsed if isinstance(parsed, dict) else {}
    except Exception as e:
        raise EnvioError(f'No se pudo contactar a GHL ({type(e).__name__}).')


def buscar_contacto_ghl(telefono, *, opener=None):
    """contactId de GHL para ese teléfono, o None. Nunca crea el contacto."""
    loc = _ghl_location()
    num = _digitos(telefono)
    if not (loc and _ghl_token() and len(num) >= 10):
        return None
    st, d = _ghl_call('GET', '/contacts/search/duplicate',
                      params={'locationId': loc, 'number': '+' + num}, opener=opener)
    if st != 200 or not isinstance(d, dict):
        if st in (400, 404):
            return None
        if st in (401, 403):
            raise EnvioError('El envío por GHL no está autorizado en el servidor.')
        if st >= 500:
            raise EnvioError(f'GHL no respondió al buscar el contacto (HTTP {st}).')
        return None
    c = d.get('contact')
    if isinstance(c, dict) and c.get('id'):
        return str(c['id'])
    if isinstance(d.get('contacts'), list):
        for item in d['contacts']:
            if isinstance(item, dict) and item.get('id'):
                return str(item['id'])
    return None


def enviar_wa_ghl(to, texto, *, opener=None):
    """Envía un texto por la API de GHL (WhatsApp). Devuelve wamid 'ghl:{messageId}'. No crea contactos."""
    if not ghl_envio_configurado():
        raise EnvioError('El envío por WhatsApp no está configurado en el servidor.')
    cid = buscar_contacto_ghl(to, opener=opener)
    if not cid:
        raise EnvioError('GHL no tiene un contacto con ese número. No se crea el contacto ni se inicia la conversación.')
    st, d = _ghl_call('POST', '/conversations/messages',
                      body={'type': 'WhatsApp', 'contactId': cid, 'message': texto}, opener=opener)
    if st in (401, 403):
        raise EnvioError('El envío por GHL no está autorizado en el servidor.', st)
    if st not in (200, 201) or not isinstance(d, dict):
        raise EnvioError(f'GHL rechazó el envío (HTTP {st}).', st)
    mid = d.get('messageId') or ((d.get('messageIds') or [None])[0])
    if not mid:
        raise EnvioError('GHL no devolvió el id del mensaje.')
    return 'ghl:' + str(mid)


def enviar_wa_meta(to, texto, *, opener=None):
    """Envía un texto libre por Cloud API. Devuelve el wamid. Nunca devuelve ni registra el token."""
    tok, pid = os.environ.get('META_WA_TOKEN'), os.environ.get('WA_PHONE_NUMBER_ID')
    if not (tok and pid):
        raise EnvioError('El envío por WhatsApp no está configurado en el servidor.')
    body = json.dumps({'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': _digitos(to),
                       'type': 'text', 'text': {'preview_url': False, 'body': texto}}, ensure_ascii=False).encode()
    req = urllib.request.Request(f'https://graph.facebook.com/{_graph_version()}/{pid}/messages', data=body,
                                 method='POST', headers={'Authorization': 'Bearer ' + tok,
                                                         'Content-Type': 'application/json'})
    op = opener or urllib.request.urlopen
    try:
        with op(req, timeout=20) as r:
            d = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        code = None
        try:
            code = (json.loads(e.read() or b'{}').get('error') or {}).get('code')
        except Exception:
            pass
        raise EnvioError(_META_ERRORES.get(code, f'Meta rechazó el envío (HTTP {e.code}).'), code)
    except Exception as e:
        raise EnvioError(f'No se pudo contactar a Meta ({type(e).__name__}).')
    wamid = ((d.get('messages') or [{}])[0] or {}).get('id')
    if not wamid:
        raise EnvioError('Meta no devolvió el id del mensaje.')
    return wamid


def enviar_wa(to, texto, *, opener=None):
    """Envía por GHL o Meta según el proveedor configurado. Devuelve el id (wamid o ghl:messageId)."""
    prov = proveedor_envio()
    if prov == 'ghl' and ghl_envio_configurado():
        return enviar_wa_ghl(to, texto, opener=opener)
    if prov == 'meta' and wa_meta_configurado():
        return enviar_wa_meta(to, texto, opener=opener)
    if ghl_envio_configurado():
        return enviar_wa_ghl(to, texto, opener=opener)
    if wa_meta_configurado():
        return enviar_wa_meta(to, texto, opener=opener)
    raise EnvioError('El envío por WhatsApp no está configurado en el servidor.')


_rate = {}
_rate_lock = threading.Lock()


def limite_ok(slug, now=None):
    now = now or time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(slug, []) if now - t < 60]
        if len(hits) >= ENVIOS_POR_MINUTO:
            _rate[slug] = hits
            return False
        hits.append(now)
        _rate[slug] = hits
        return True


# ------------------------------------------------------------------ Instagram / Facebook (solo lectura)
def social_activo():
    return os.environ.get('META_SOCIAL_INBOX') == '1' and bool(_page_token())


def _page_token():
    tok = os.environ.get('META_PAGE_TOKEN')
    if tok:
        return tok.strip()
    p = os.environ.get('META_PAGE_TOKEN_FILE')
    if p and os.path.exists(p):
        try:
            return open(p, encoding='utf-8').read().strip()
        except Exception:
            return ''
    return ''


def _page_id():
    return os.environ.get('META_PAGE_ID') or META_PAGE_ID_DEF


def _ig_id():
    return os.environ.get('META_IG_ID') or META_IG_ID_DEF


_cache = {}
_cache_lock = threading.Lock()
CACHE_SEG = 60


def _graph_get(path, params, *, opener=None):
    params = dict(params)
    params['access_token'] = _page_token()
    url = f'https://graph.facebook.com/{_graph_version()}/{path}?' + urllib.parse.urlencode(params)
    op = opener or urllib.request.urlopen
    with op(urllib.request.Request(url, method='GET'), timeout=20) as r:
        return json.loads(r.read() or b'{}')


def _cached(key, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SEG:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
    return val


def _otro(participantes, propios):
    for p in participantes or []:
        if str(p.get('id')) not in propios:
            return p
    return (participantes or [{}])[0]


def social_conversaciones(*, opener=None, limite=25):
    """Lista conversaciones IG y FB (lectura). Ids de conversación con prefijo ig: / fb:. Errores -> lista vacía."""
    if not social_activo():
        return []
    propios = {_page_id(), _ig_id()}
    out = []
    for plat, canal, pref in (('messenger', 'facebook', 'fb'), ('instagram', 'instagram', 'ig')):
        def cargar(plat=plat):
            return _graph_get(f'{_page_id()}/conversations', {
                'platform': plat, 'limit': str(limite),
                'fields': 'id,updated_time,participants,messages.limit(1){message,from,created_time}'}, opener=opener)
        try:
            d = _cached(('convs', plat), cargar)
        except Exception:
            continue
        for c in d.get('data') or []:
            parts = ((c.get('participants') or {}).get('data')) or []
            otro = _otro(parts, propios)
            m = (((c.get('messages') or {}).get('data')) or [{}])[0]
            de_nosotros = str((m.get('from') or {}).get('id')) in propios
            out.append({
                'id': f"{pref}:{c.get('id')}",
                'canal': canal,
                'nombre': otro.get('name') or otro.get('username') or 'Cliente',
                'texto': m.get('message') or '',
                'saliente': de_nosotros,
                'ts': m.get('created_time') or c.get('updated_time') or '',
            })
    return out


def social_hilo(conv_id, *, opener=None, limite=50):
    """Mensajes de una conversación IG/FB (lectura). conv_id con prefijo ig:/fb:."""
    if not social_activo() or ':' not in (conv_id or ''):
        return None
    pref, cid = conv_id.split(':', 1)
    if pref not in ('ig', 'fb') or not re.fullmatch(r'[A-Za-z0-9_\-=]+', cid or ''):
        return None
    propios = {_page_id(), _ig_id()}
    try:
        d = _cached(('hilo', cid), lambda: _graph_get(f'{cid}/messages', {
            'limit': str(limite), 'fields': 'id,message,from,created_time'}, opener=opener))
    except Exception:
        return None
    msgs = []
    for m in reversed(d.get('data') or []):
        msgs.append({'texto': m.get('message') or '', 'ts': m.get('created_time') or '',
                     'saliente': str((m.get('from') or {}).get('id')) in propios,
                     'de': (m.get('from') or {}).get('name') or (m.get('from') or {}).get('username') or ''})
    return {'canal': 'instagram' if pref == 'ig' else 'facebook', 'mensajes': msgs}
