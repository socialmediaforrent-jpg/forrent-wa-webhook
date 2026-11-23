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


def en_pruebas():
    return ('pytest' in sys.modules) or bool(os.environ.get('PYTEST_CURRENT_TEST'))


def desactivado():
    return os.environ.get('CLIENTES_SYNC_DISABLED') == '1'


def estatus_url():
    return (os.environ.get('CLIENTES_ESTATUS_SYNC_URL') or '').rstrip('/')


def estatus_key():
    return (os.environ.get('CLIENTES_ESTATUS_SYNC_KEY') or '').strip()


def configurado():
    return bool(estatus_url() and estatus_key())


def intervalo():
    try:
        n = int(os.environ.get('CLIENTES_SYNC_INTERVAL_SEC') or INTERVALO_DEF)
    except ValueError:
        n = INTERVALO_DEF
    return max(60, min(n, INTERVALO_MAX))


def ahora_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _guardar_portal(q, is_pg, nombre, datos):
    act = datos.get('actualizado') or datos.get('generado_at') or ahora_iso()
    if is_pg:
        q("""INSERT INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)
             ON CONFLICT (nombre) DO UPDATE SET datos = EXCLUDED.datos, actualizado = EXCLUDED.actualizado""",
          (nombre, datos, act))
    else:
        q('INSERT OR REPLACE INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)',
          (nombre, datos, act))


def leer_respuestas(opener=None):
    """Descarga las respuestas vigentes del Sistema de Clientes. No imprime el cuerpo."""
    url = estatus_url() + '/sync/respuestas'
    key = estatus_key()
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
    if not configurado():
        resumen['error'] = 'no configurado'
        return resumen
    resp = leer_respuestas(opener=opener)
    data = None
    # Preferir la lista que el portal ya sirve (CRM si está listo).
    if also_crm:
        try:
            import crm as _crm
            if _crm.crm_listo(q):
                data = _crm.portal_payload('clientes_asignados.json', q)
        except Exception:
            log.exception('clientes_sync crm payload')
            data = None
    if data is None:
        data = _cargar_asignados(q)
    if not data:
        resumen['error'] = 'sin lista'
        return resumen
    nuevo, n_cam = aplicar_a_payload(data, resp)
    _guardar_portal(q, is_pg, 'clientes_asignados.json', nuevo)
    if also_crm:
        try:
            _aplicar_estatus_crm(q, nuevo.get('clientes') or [])
        except Exception:
            log.exception('clientes_sync crm estatus')
    resumen.update(ok=True, cambiados=n_cam, n=len(nuevo.get('clientes') or []),
                   actualizado=nuevo.get('actualizado') or '')
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
    if en_pruebas() or desactivado() or not configurado():
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
    if en_pruebas() or desactivado() or not configurado():
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
            _ultima = time.time()
            _pedir.clear()
            _parar.wait(1)

    _hilo = threading.Thread(target=loop, name='clientes-sync', daemon=True)
    _hilo.start()
    return True


def avisar():
    _pedir.set()
