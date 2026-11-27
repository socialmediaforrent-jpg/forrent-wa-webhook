"""Sincroniza asignaciones y estatus hacia el almacén vivo del portal.

Fuente de estatus: Sistema de Clientes (GET /sync/respuestas).
Se corre en un hilo del propio servicio web, cada 15 minutos como máximo,
y también al cargar Mis Asignaciones / Mis Clientes si el sello está vencido.
No crea un cron ni un servicio de pago nuevo. Se apaga con
CLIENTES_SYNC_DISABLED=1 o si faltan las credenciales del Sistema de Clientes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger('clientes_sync')

TZ = ZoneInfo('America/Caracas')
INTERVALO_DEF = 900  # 15 minutos
INTERVALO_MAX = 900
EXTRA_WEB_NO_AVANCE = frozenset({'', 'sin estatus', 'asignado', 'no contactado', 'nuevo'})

_lock = threading.Lock()
_hilo = None
_parar = threading.Event()
_pedir = threading.Event()
_ultima = 0.0
_ultimo = {}
SELLO_NOMBRE = 'clientes_sync_ok'


def en_pruebas():
    return ('pytest' in sys.modules) or bool(os.environ.get('PYTEST_CURRENT_TEST'))


def desactivado():
    return os.environ.get('CLIENTES_SYNC_DISABLED') == '1'


def _conf_meta(q):
    try:
        import crm as _crm
        d = _crm.estatus_sync_conf(q) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def estatus_url(q=None):
    u = (os.environ.get('CLIENTES_ESTATUS_SYNC_URL') or '').rstrip('/')
    if u:
        return u
    if q is not None:
        return str((_conf_meta(q).get('url') or '')).rstrip('/')
    return ''


def estatus_key(q=None):
    k = (os.environ.get('CLIENTES_ESTATUS_SYNC_KEY') or '').strip()
    if k:
        return k
    if q is not None:
        return str((_conf_meta(q).get('key') or '')).strip()
    return ''


def configurado(q=None):
    return bool(estatus_url(q) and estatus_key(q))


def intervalo():
    try:
        n = int(os.environ.get('CLIENTES_SYNC_INTERVAL_SEC') or INTERVALO_DEF)
    except ValueError:
        n = INTERVALO_DEF
    return max(60, min(n, INTERVALO_MAX))


def ahora_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


def reset_memoria():
    """Solo para pruebas: olvida el sello en memoria."""
    global _ultima, _ultimo
    _ultima = 0.0
    _ultimo = {}


def _guardar_sello(q, is_pg, resumen):
    """Guarda la hora de una sincronización exitosa, haya o no cambios."""
    datos = {
        'ok': True,
        'actualizado': resumen.get('actualizado') or ahora_iso(),
        'n': resumen.get('n') or 0,
        'cambiados': resumen.get('cambiados') or 0,
    }
    try:
        _guardar_portal(q, is_pg, SELLO_NOMBRE, datos)
    except Exception:
        log.exception('clientes_sync sello portal')
    try:
        import crm as _crm
        if _crm.crm_listo(q):
            _crm.set_meta(q, 'clientes_sync_ok', datos)
    except Exception:
        pass


def _leer_sello_guardado(q):
    if not q:
        return ''
    try:
        rows = q("SELECT datos FROM portal_datos WHERE nombre=%s", (SELLO_NOMBRE,), fetch=True) or []
        if rows:
            d = rows[0]['datos']
            if isinstance(d, str):
                d = json.loads(d)
            if isinstance(d, dict) and d.get('ok') and d.get('actualizado'):
                return d['actualizado']
    except Exception:
        pass
    try:
        import crm as _crm
        m = _crm._meta(q, 'clientes_sync_ok', {}) or {}
        if isinstance(m, dict) and m.get('actualizado') and m.get('ok', True):
            return m['actualizado']
    except Exception:
        pass
    return ''


def sello_actualizado(q=None):
    """ISO de la última sincronización exitosa. Vacío si todavía no hubo una."""
    if (_ultimo or {}).get('ok') and (_ultimo or {}).get('actualizado'):
        return _ultimo['actualizado']
    return _leer_sello_guardado(q)


def _guardar_portal(q, is_pg, nombre, datos):
    act = datos.get('actualizado') or datos.get('generado_at') or ahora_iso()
    if is_pg:
        q("""INSERT INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)
             ON CONFLICT (nombre) DO UPDATE SET datos = EXCLUDED.datos, actualizado = EXCLUDED.actualizado""",
          (nombre, datos, act))
    else:
        q('INSERT OR REPLACE INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)',
          (nombre, datos, act))


def leer_respuestas(opener=None, q=None):
    """Descarga las respuestas vigentes del Sistema de Clientes. No imprime el cuerpo."""
    url = estatus_url(q) + '/sync/respuestas'
    key = estatus_key(q)
    req = urllib.request.Request(
        url,
        headers={'X-Sync-Key': key, 'User-Agent': 'forrent-clientes-sync', 'Accept': 'application/json'},
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=45) as resp:
        raw = resp.read() or b'{}'
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def aplicar_respuestas(clientes, resp):
    """Aplica el estatus vigente del Sistema de Clientes sobre la lista de asignados."""
    out = []
    cambiado = 0
    for c in clientes or []:
        d = dict(c)
        rec = resp.get(d.get('id') or '')
        if isinstance(rec, dict) and rec.get('estado'):
            estado = rec.get('estado') or d.get('estado') or ''
            sit = rec.get('situacion') or ''
            nuevo_est = sit or estado
            if (d.get('estatus') or '') != nuevo_est or (d.get('estado') or '') != estado or (d.get('situacion') or '') != sit:
                cambiado += 1
            d['estado'] = estado
            d['situacion'] = sit
            d['estatus'] = nuevo_est
        out.append(d)
    return out, cambiado


def aplicar_a_payload(data, resp):
    data = dict(data or {})
    clientes, n = aplicar_respuestas(data.get('clientes') or [], resp)
    data['clientes'] = clientes
    data['actualizado'] = ahora_iso()
    data['respuestas_en_vivo'] = True
    data['fuente'] = data.get('fuente') or 'CRM propia y Sistema de Clientes'
    return data, n


def _cargar_asignados(q):
    rows = q("SELECT datos FROM portal_datos WHERE nombre=%s", ('clientes_asignados.json',), fetch=True) or []
    if not rows:
        return None
    d = rows[0]['datos']
    if isinstance(d, str):
        d = json.loads(d)
    return d if isinstance(d, dict) else None


def pasada(q, *, is_pg=False, opener=None, also_crm=True):
    """Una pasada: lee estatus en vivo y actualiza el almacén del portal.

    Si el CRM propio está cargado, también refresca estatus en crm_contacts
    (sin borrar filas ni tocar otras tablas).
    """
    resumen = {'ok': False, 'cambiados': 0, 'n': 0, 'actualizado': ''}
    if not configurado(q):
        resumen['error'] = 'no configurado'
        return resumen
    resp = leer_respuestas(opener=opener, q=q)
    data = None
    # Preferir la lista que el portal ya sirve (CRM si está listo).
    crm_listo = False
    if also_crm:
        try:
            import crm as _crm
            crm_listo = bool(_crm.crm_listo(q))
            if crm_listo:
                data = _crm.portal_payload('clientes_asignados.json', q)
        except Exception:
            log.exception('clientes_sync crm payload')
            # Si el CRM está cargado, no caemos a una copia vieja de portal_datos.
            resumen['error'] = 'crm payload'
            return resumen
    if data is None and not crm_listo:
        data = _cargar_asignados(q)
    if not data:
        resumen['error'] = 'sin lista'
        return resumen
    nuevo, n_cam = aplicar_a_payload(data, resp)
    _guardar_portal(q, is_pg, 'clientes_asignados.json', nuevo)
    try:
        guardar_historial(q, is_pg, resp, [c.get('id') for c in (nuevo.get('clientes') or [])])
    except Exception:
        log.exception('clientes_sync historial')
    if also_crm:
        try:
            _aplicar_estatus_crm(q, nuevo.get('clientes') or [])
        except Exception:
            log.exception('clientes_sync crm estatus')
    resumen.update(ok=True, cambiados=n_cam, n=len(nuevo.get('clientes') or []),
                   actualizado=nuevo.get('actualizado') or '')
    global _ultimo
    _ultimo = dict(resumen)
    _guardar_sello(q, is_pg, resumen)
    log.info('clientes_sync ok n=%s cambiados=%s actualizado=%s',
             resumen['n'], resumen['cambiados'], resumen['actualizado'])
    return resumen


def _aplicar_estatus_crm(q, clientes):
    """Actualiza solo estatus/estado/situación de contactos ya existentes."""
    for c in clientes:
        cid = c.get('id') or ''
        if not cid:
            continue
        q("""UPDATE crm_contacts SET
               estatus=%s, estado=%s, situacion=%s, updated_at=NOW()
             WHERE (ghl_id=%s OR registro_id=%s)
               AND (
                    COALESCE(estatus,'') IS DISTINCT FROM %s
                    OR COALESCE(estado,'') IS DISTINCT FROM %s
                    OR COALESCE(situacion,'') IS DISTINCT FROM %s
               )""",
          (c.get('estatus') or '', c.get('estado') or '', c.get('situacion') or '',
           cid, cid, c.get('estatus') or '', c.get('estado') or '', c.get('situacion') or ''))


def maybe_refresh(q, *, is_pg=False, invalidate=None):
    """Si la última pasada tiene más de `intervalo` segundos, corre una ahora.

    Pensado para la carga de página. Si no está configurado o corre pytest, no hace nada.
    """
    global _ultima, _ultimo
    if en_pruebas() or desactivado() or not configurado(q):
        return None
    if _ultima and (time.time() - _ultima) < intervalo():
        return _ultimo
    with _lock:
        if _ultima and (time.time() - _ultima) < intervalo():
            return _ultimo
        try:
            _ultimo = pasada(q, is_pg=is_pg) or {}
        except Exception:
            log.exception('clientes_sync maybe_refresh')
            _ultimo = {'ok': False, 'error': 'fallo'}
        _ultima = time.time()
        if invalidate:
            try:
                invalidate()
            except Exception:
                pass
        return _ultimo


def iniciar_fondo(app_factory, *, is_pg, invalidate=None):
    """Arranca un hilo daemon. app_factory() debe devolver (q,). No se usa en pytest."""
    global _hilo
    if en_pruebas() or desactivado() or not (configurado() or is_pg):
        return False
    if _hilo and _hilo.is_alive():
        return True

    def loop():
        global _ultima, _ultimo
        _parar.wait(8)  # no competir con /healthz al arrancar
        while not _parar.is_set():
            disparo = _pedir.is_set()
            _pedir.clear()
            if not disparo and (time.time() - _ultima) < intervalo() and _ultima:
                _parar.wait(1)
                continue
            try:
                q, = app_factory()
                _ultimo = pasada(q, is_pg=is_pg) or {}
                if invalidate:
                    invalidate()
            except Exception:
                log.exception('clientes_sync fondo')
                _ultimo = {'ok': False, 'error': 'fallo'}
            if (_ultimo or {}).get('error') != 'no configurado':
                _ultima = time.time()
            _pedir.clear()
            _parar.wait(1)

    _hilo = threading.Thread(target=loop, name='clientes-sync', daemon=True)
    _hilo.start()
    return True


def avisar():
    _pedir.set()


# ---------------------------------------------------------------- Tarjeta del cliente (27-nov-2026)
# Historial y actualización de la Situación desde For Rent Online, con el mismo Sistema de Clientes.
# Fuente única: respuestas del Sistema de Clientes (GET /sync/respuestas, POST /sync/data con la misma
# llave X-Sync-Key). La app no guarda estatus por su cuenta: copia lo que el Sistema confirma.
HIST_NOMBRE = 'clientes_historial'
_HIST_CAMPOS = ('estado', 'situacion', 'estatus', 'fecha', 'motivo', 'comentario', 'at', 'asesor', 'por', 'via')


def _slim(rec):
    return {k: rec[k] for k in _HIST_CAMPOS if isinstance(rec, dict) and rec.get(k)}


def historial_de_respuestas(resp, ids):
    """{id: [entradas, la más reciente al final]} solo para los clientes indicados."""
    out = {}
    for cid in ids or []:
        r = (resp or {}).get(cid or '')
        if not isinstance(r, dict) or not (r.get('estado') or r.get('estatus')):
            continue
        h = [_slim(x) for x in (r.get('historial') or []) if isinstance(x, dict)]
        h = [x for x in h if x.get('estado') or x.get('estatus')]
        out[cid] = h + [_slim(r)]
    return out


def leer_historial(q):
    if not q:
        return {}
    try:
        rows = q('SELECT datos FROM portal_datos WHERE nombre=%s', (HIST_NOMBRE,), fetch=True) or []
        if rows:
            d = rows[0]['datos']
            if isinstance(d, str):
                d = json.loads(d)
            if isinstance(d, dict):
                return d.get('clientes') or {}
    except Exception:
        pass
    return {}


def guardar_historial(q, is_pg, resp, ids):
    nuevo = historial_de_respuestas(resp, ids)
    # Clientes que el Sistema no trae (p. ej. uno de prueba en la copia de prueba): se conserva lo local.
    previo = leer_historial(q)
    for cid in ids or []:
        if cid and cid not in nuevo and cid in previo:
            nuevo[cid] = previo[cid]
    _guardar_portal(q, is_pg, HIST_NOMBRE, {'actualizado': ahora_iso(), 'clientes': nuevo})
    return nuevo


def escritura_habilitada():
    """Solo producción envía al Sistema de Clientes. En la copia de prueba queda en la base de prueba."""
    return os.environ.get('CLIENTES_ESTATUS_WRITE') == '1'


def enviar_respuesta(q, cid, rec, opener=None):
    """Manda una entrada nueva al Sistema de Clientes por /sync/data (misma llave que la lectura).

    El Sistema la une con merge_resp: la más reciente queda vigente y la anterior pasa al historial.
    """
    if not configurado(q):
        raise RuntimeError('Sistema de Clientes no configurado')
    url = estatus_url(q) + '/sync/data'
    body = json.dumps({'respuestas': {cid: rec}}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url, data=body, method='POST',
        headers={'X-Sync-Key': estatus_key(q), 'Content-Type': 'application/json',
                 'User-Agent': 'forrent-online-ficha', 'Accept': 'application/json'},
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=30) as resp:
        d = json.loads(resp.read() or b'{}')
    if not (isinstance(d, dict) and d.get('ok')):
        raise RuntimeError('El Sistema de Clientes no confirmó el guardado')
    return True


def aplicar_local(q, is_pg, cid, rec):
    """Refleja de inmediato la entrada nueva en lo que sirve la app (lista, CRM e historial)."""
    global _ultima
    estado = rec.get('estado') or ''
    sit = rec.get('situacion') or ''
    est = sit or estado
    data = _cargar_asignados(q)
    if data:
        cambiado = False
        for c in data.get('clientes') or []:
            if (c.get('id') or '') == cid:
                c['estado'], c['situacion'], c['estatus'] = estado, sit, est
                cambiado = True
        if cambiado:
            data['actualizado'] = ahora_iso()
            _guardar_portal(q, is_pg, 'clientes_asignados.json', data)
    try:
        _aplicar_estatus_crm(q, [{'id': cid, 'estado': estado, 'situacion': sit, 'estatus': est}])
    except Exception:
        pass
    hist = leer_historial(q)
    lst = list(hist.get(cid) or [])
    lst.append(_slim(rec))
    hist[cid] = lst
    _guardar_portal(q, is_pg, HIST_NOMBRE, {'actualizado': ahora_iso(), 'clientes': hist})
    # Que la próxima carga de página no pise el cambio con una pasada inmediata.
    _ultima = time.time()
    return est
