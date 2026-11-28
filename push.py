"""Avisos push (Web Push, PWA) al asesor cuando se le asigna un cliente.

- Claves VAPID en variables de entorno del servicio: VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT.
  Sin ellas el módulo queda apagado (no falla nada).
- Suscripciones por asesor y dispositivo (tabla push_subs). Las vencidas (404/410) se borran solas.
- Detección de asignaciones nuevas aparte del correo: tabla push_asig_vistas. La primera vez solo marca
  la base (silencioso) para no avisar de clientes que ya estaban asignados.
"""
from __future__ import annotations

import json
import logging
import os
from urllib.parse import quote

log = logging.getLogger('push')

TTL = 60 * 60 * 24  # el servicio de push guarda el aviso hasta 24 h si el teléfono está apagado
MAX_SUBS_ASESOR = 10


def public_key() -> str:
    return (os.environ.get('VAPID_PUBLIC_KEY') or '').strip()


def activo() -> bool:
    return bool(public_key() and (os.environ.get('VAPID_PRIVATE_KEY') or '').strip())


def init(q, serial):
    q(f"""CREATE TABLE IF NOT EXISTS push_subs (
        id {serial}, asesor_slug TEXT NOT NULL, endpoint TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
        ua TEXT, created_at TEXT NOT NULL, last_ok TEXT)""")
    q("CREATE UNIQUE INDEX IF NOT EXISTS push_subs_endpoint ON push_subs (endpoint)")
    q("""CREATE TABLE IF NOT EXISTS push_asig_vistas (
        asesor_slug TEXT NOT NULL, cliente_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (asesor_slug, cliente_id))""")


def _sub_valida(sub):
    if not isinstance(sub, dict):
        return None
    ep = str(sub.get('endpoint') or '')
    keys = sub.get('keys') if isinstance(sub.get('keys'), dict) else {}
    p, a = str(keys.get('p256dh') or ''), str(keys.get('auth') or '')
    if not ep.startswith('https://') or len(ep) > 600 or not p or not a or len(p) > 200 or len(a) > 100:
        return None
    return ep, p, a


def suscribir(q, slug, sub, ua, ahora):
    v = _sub_valida(sub)
    if not v or not slug:
        return False
    ep, p, a = v
    rows = q('SELECT id FROM push_subs WHERE endpoint=%s', (ep,), fetch=True) or []
    if rows:
        q('UPDATE push_subs SET asesor_slug=%s, p256dh=%s, auth=%s, ua=%s WHERE endpoint=%s', (slug, p, a, (ua or '')[:200], ep))
    else:
        q('INSERT INTO push_subs (asesor_slug, endpoint, p256dh, auth, ua, created_at) VALUES (%s,%s,%s,%s,%s,%s)',
          (slug, ep, p, a, (ua or '')[:200], ahora))
        # Tope por asesor: se conservan los dispositivos más recientes.
        todas = q('SELECT id FROM push_subs WHERE asesor_slug=%s ORDER BY id DESC', (slug,), fetch=True) or []
        for r in todas[MAX_SUBS_ASESOR:]:
            q('DELETE FROM push_subs WHERE id=%s', (r['id'],))
    return True


def desuscribir(q, slug, endpoint):
    if not endpoint:
        return
    q('DELETE FROM push_subs WHERE endpoint=%s AND asesor_slug=%s', (str(endpoint), slug))


def tiene(q, slug, endpoint):
    rows = q('SELECT id FROM push_subs WHERE endpoint=%s AND asesor_slug=%s', (str(endpoint or ''), slug), fetch=True) or []
    return bool(rows)


def n_dispositivos(q, slug):
    return len(q('SELECT id FROM push_subs WHERE asesor_slug=%s', (slug,), fetch=True) or [])


def _enviar_uno(sub_info, payload):
    """Envía un push. Devuelve el código HTTP del servicio de push (201 = aceptado) o 0 si falló antes."""
    from pywebpush import webpush, WebPushException
    try:
        r = webpush(
            subscription_info=sub_info,
            data=payload,
            vapid_private_key=os.environ.get('VAPID_PRIVATE_KEY', '').strip(),
            vapid_claims={'sub': os.environ.get('VAPID_SUBJECT') or 'mailto:socialmediaforrent@gmail.com'},
            ttl=TTL,
            timeout=10,
        )
        return int(getattr(r, 'status_code', 0) or 0)
    except WebPushException as e:
        resp = getattr(e, 'response', None)
        return int(getattr(resp, 'status_code', 0) or 0)
    except Exception:
        log.exception('push envío')
        return 0


def avisar(q, slug, titulo, cuerpo, url, ahora='', tag=''):
    """Envía a todos los dispositivos del asesor. Borra los vencidos. Devuelve la lista de códigos."""
    if not activo():
        return []
    subs = q('SELECT id, endpoint, p256dh, auth FROM push_subs WHERE asesor_slug=%s', (slug,), fetch=True) or []
    payload = json.dumps({'title': titulo, 'body': cuerpo, 'url': url, 'tag': tag or 'for-rent'}, ensure_ascii=False)
    codigos = []
    for s in subs:
        code = _enviar_uno({'endpoint': s['endpoint'], 'keys': {'p256dh': s['p256dh'], 'auth': s['auth']}}, payload)
        codigos.append(code)
        if code in (404, 410):
            q('DELETE FROM push_subs WHERE id=%s', (s['id'],))
        elif 200 <= code < 300:
            q('UPDATE push_subs SET last_ok=%s WHERE id=%s', (ahora, s['id']))
    return codigos


def url_ficha(cid):
    return '/mis-clientes?abrir=' + quote(str(cid), safe='')


def revisar_asignaciones(q, asignados, nombre_de=None, ahora=''):
    """Compara la lista vigente con las ya vistas. Primera vez: solo marca la base.
    Marca todo como visto aunque el asesor no tenga dispositivos (no se avisa tarde de lo viejo).
    Devuelve {'base': bool, 'nuevos': n, 'enviados': n, 'codigos': [...]}."""
    vistos = {(r['asesor_slug'], r['cliente_id']) for r in (q('SELECT asesor_slug, cliente_id FROM push_asig_vistas', fetch=True) or [])}
    base = not vistos
    nuevos, enviados, codigos = 0, 0, []
    for c in asignados or []:
        key = (str(c.get('asesor') or ''), str(c.get('id') or ''))
        if not all(key) or key in vistos:
            continue
        q('INSERT INTO push_asig_vistas (asesor_slug, cliente_id, created_at) VALUES (%s,%s,%s)', key + (ahora,))
        vistos.add(key)
        if base:
            continue
        nuevos += 1
        nombre = str(c.get('nombre') or 'cliente').strip() or 'cliente'
        try:
            cs = avisar(q, key[0], 'For Rent Online', f'Nuevo cliente asignado: {nombre}', url_ficha(key[1]), ahora, tag='asig-' + key[1])
        except Exception:
            log.exception('push asignación')
            cs = []
        codigos += cs
        enviados += sum(1 for x in cs if 200 <= x < 300)
    return {'base': base, 'nuevos': nuevos, 'enviados': enviados, 'codigos': codigos}
