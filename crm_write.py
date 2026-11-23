"""Escrituras en tablas crm_*. No avanza la rueda: solo copia el índice ya guardado.
Nunca debe romper el flujo que la llama (el caller envuelve en try/except).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Caracas')

RUEDAS = [
    ('busqueda', 'index'),
    ('vpc', 'index_vpc'),
    ('captacion', 'index_captacion'),
    ('consulta_pc', 'index_consulta_pc'),
    ('consulta_ml_pc', 'index_consulta_ml_pc'),
]


def _now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _phone_norm(s):
    digits = re.sub(r'\D', '', s or '')
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0') and len(digits) == 11:
        digits = '58' + digits[1:]
    return digits or None


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def _slug_map(q):
    """Usa asesores del portal (data/asesores.json vía portal_lib si está)."""
    try:
        import portal_lib
        m = {}
        for a in portal_lib.asesores_meta():
            for n in [a.get('name'), a.get('user')] + list(a.get('aliases') or []):
                if n:
                    m[portal_lib.norm(n)] = a['user']
        return m, portal_lib
    except Exception:
        return {}, None


def contact_pk(q, *, ghl_id=None, registro_id=None, propio_id=None):
    if ghl_id:
        rows = q('SELECT id FROM crm_contacts WHERE ghl_id=%s', (ghl_id,), fetch=True) or []
        if rows:
            return rows[0]['id']
    if registro_id:
        rows = q('SELECT id FROM crm_contacts WHERE registro_id=%s', (registro_id,), fetch=True) or []
        if rows:
            return rows[0]['id']
    if propio_id:
        rows = q('SELECT id FROM crm_contacts WHERE propio_id=%s', (str(propio_id),), fetch=True) or []
        if rows:
            return rows[0]['id']
    return None


def upsert_contact(q, data: dict) -> int:
    """Crea o actualiza un contacto. data puede traer ghl_id, registro_id, name, phone, asesor, etc."""
    slugs, plib = _slug_map(q)
    gid = data.get('ghl_id') or None
    rid = data.get('registro_id') or gid
    pk = contact_pk(q, ghl_id=gid, registro_id=rid)
    asesor_nombre = data.get('asesor_nombre') or data.get('asesor') or None
    slug = data.get('asesor_slug')
    if not slug and asesor_nombre and plib:
        slug = slugs.get(plib.norm(asesor_nombre))
    phone = data.get('phone') or data.get('telefono')
    aa = data.get('asignado_at') or data.get('asignado_at_raw')
    if pk is None:
        rows = q(
            """INSERT INTO crm_contacts (
                 ghl_id, registro_id, phone, phone_normalized, name, source_kind,
                 tipo_lead, asesor_slug, asesor_nombre, asignado_at_raw, asignado_at,
                 inmueble, operacion, origen, estado, situacion, estatus, in_registro,
                 excluido, propio_id, created_at, updated_at
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,NOW(),NOW())
               RETURNING id""",
            (gid, rid, phone, _phone_norm(phone or ''), data.get('name') or data.get('nombre'),
             data.get('source_kind') or ('registro' if rid and not gid else 'ghl'),
             data.get('tipo_lead'), slug, asesor_nombre, aa, _parse_dt(aa),
             data.get('inmueble'), data.get('operacion'), data.get('origen') or 'Asignado por For Rent',
             data.get('estado'), data.get('situacion'), data.get('estatus') or data.get('estado'),
             bool(data.get('in_registro', True)), data.get('propio_id')),
            fetch=True,
        )
        return rows[0]['id']
    q(
        """UPDATE crm_contacts SET
             phone=COALESCE(%s, phone),
             phone_normalized=COALESCE(%s, phone_normalized),
             name=COALESCE(NULLIF(%s,''), name),
             tipo_lead=COALESCE(%s, tipo_lead),
             asesor_slug=COALESCE(%s, asesor_slug),
             asesor_nombre=COALESCE(%s, asesor_nombre),
             asignado_at_raw=COALESCE(%s, asignado_at_raw),
             asignado_at=COALESCE(%s, asignado_at),
             inmueble=COALESCE(%s, inmueble),
             operacion=COALESCE(%s, operacion),
             origen=COALESCE(%s, origen),
             estado=COALESCE(%s, estado),
             situacion=COALESCE(%s, situacion),
             estatus=COALESCE(%s, estatus),
             in_registro=in_registro OR %s,
             registro_id=COALESCE(registro_id, %s),
             updated_at=NOW()
           WHERE id=%s""",
        (phone, _phone_norm(phone or '') if phone else None, data.get('name') or data.get('nombre'),
         data.get('tipo_lead'), slug, asesor_nombre, aa, _parse_dt(aa),
         data.get('inmueble'), data.get('operacion'), data.get('origen'),
         data.get('estado'), data.get('situacion'), data.get('estatus') or data.get('estado'),
         bool(data.get('in_registro', True)), rid, pk),
    )
    return pk


def record_assignment(q, data: dict) -> int:
    pk = upsert_contact(q, {**data, 'in_registro': True})
    q(
        """INSERT INTO crm_assignments (
             contact_id, ghl_id, asesor_nombre, asesor_slug, tipo_lead, assigned_at, source, rueda_tipo, rueda_index, note, payload
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pk, data.get('ghl_id'), data.get('asesor_nombre') or data.get('asesor'),
         data.get('asesor_slug'), data.get('tipo_lead'),
         _parse_dt(data.get('assigned_at') or data.get('asignado_at') or _now()),
         data.get('source') or 'assign_lead', data.get('rueda_tipo'), data.get('rueda_index'),
         data.get('note'), data.get('payload') if isinstance(data.get('payload'), (dict, list)) else None),
    )
    return pk


def sync_rueda(q, state: dict) -> dict:
    """Copia los índices ya guardados en el state. No suma 1."""
    out = {}
    for tipo, key in RUEDAS:
        try:
            idx = int(state.get(key) or 0)
        except (TypeError, ValueError):
            idx = 0
        q(
            """INSERT INTO crm_rueda_state (tipo, state_key, indice, orden, updated_at, raw)
               VALUES (%s,%s,%s,'[]'::jsonb,NOW(),%s)
               ON CONFLICT (tipo) DO UPDATE SET
                 indice=EXCLUDED.indice, state_key=EXCLUDED.state_key,
                 updated_at=NOW(), raw=EXCLUDED.raw""",
            (tipo, key, idx, state),
        )
        out[tipo] = idx
    return out


def upsert_phase(q, data: dict) -> None:
    gid = data.get('ghl_id') or data.get('id')
    rec = data.get('payload') or {k: data.get(k) for k in ('estado', 'situacion', 'estatus', 'at', 'asesor', 'comentario', 'motivo') if data.get(k) is not None}
    pk = contact_pk(q, ghl_id=gid, registro_id=gid) or upsert_contact(q, {
        'ghl_id': gid if not str(gid).startswith('test_') and len(str(gid) or '') >= 10 else None,
        'registro_id': gid, 'in_registro': True,
        'estado': rec.get('estado'), 'situacion': rec.get('situacion'),
        'estatus': rec.get('situacion') or rec.get('estado'),
    })
    q(
        """INSERT INTO crm_respuestas (ghl_id, contact_id, payload, updated_at)
           VALUES (%s,%s,%s,NOW())
           ON CONFLICT (ghl_id) DO UPDATE SET payload=EXCLUDED.payload, contact_id=COALESCE(EXCLUDED.contact_id, crm_respuestas.contact_id), updated_at=NOW()""",
        (gid, pk, rec),
    )
    est = rec.get('situacion') or rec.get('estado')
    q(
        """INSERT INTO crm_phase_events (contact_id, ghl_id, estado, situacion, estatus, at, source, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (pk, gid, rec.get('estado'), rec.get('situacion'), est,
         _parse_dt(rec.get('at') or data.get('at') or _now()),
         data.get('source') or 'estatus', rec),
    )
    q(
        """UPDATE crm_contacts SET estado=%s, situacion=%s, estatus=%s, updated_at=NOW() WHERE id=%s""",
        (rec.get('estado'), rec.get('situacion'), est, pk),
    )


def insert_visit(q, data: dict) -> None:
    pk = contact_pk(q, ghl_id=data.get('ghl_id'), registro_id=data.get('registro_id') or data.get('ghl_id'))
    q(
        """INSERT INTO crm_visits (contact_id, ghl_id, asesor_slug, fecha, hora, cliente, inmueble, nota, created_at, source, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s)""",
        (pk, data.get('ghl_id'), data.get('asesor_slug'), data.get('fecha'), data.get('hora'),
         data.get('cliente'), data.get('inmueble'), data.get('nota'),
         data.get('source') or 'portal', data if isinstance(data, dict) else None),
    )


def insert_offer(q, data: dict) -> None:
    pk = contact_pk(q, ghl_id=data.get('ghl_id'), registro_id=data.get('registro_id') or data.get('ghl_id'))
    q(
        """INSERT INTO crm_offers (contact_id, ghl_id, asesor_slug, tipo, cliente, inmueble, monto, fecha, nota, created_at, source, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s)""",
        (pk, data.get('ghl_id'), data.get('asesor_slug'), data.get('tipo'), data.get('cliente'),
         data.get('inmueble'), data.get('monto'), data.get('fecha'), data.get('nota'),
         data.get('source') or 'portal', data if isinstance(data, dict) else None),
    )


def insert_reservation(q, data: dict) -> None:
    q(
        """INSERT INTO crm_reservations (ghl_id, asesor_slug, asesor_nombre, fecha, operacion, inmueble, codigo, wasi, origen, estado, cliente, comision, source, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (data.get('ghl_id') or data.get('id'), data.get('asesor_slug'), data.get('asesor_nombre') or data.get('asesor'),
         data.get('fecha') or data.get('fecha_reserva') or data.get('fecha_firma'),
         data.get('operacion') or data.get('tipo'), data.get('inmueble'),
         str(data.get('codigo') or ''), str(data.get('wasi') or ''),
         data.get('origen'), data.get('estado'), data.get('cliente') or data.get('arrendatario'),
         data.get('comision') if isinstance(data.get('comision'), dict) else {},
         data.get('source') or 'cierres_seguimiento', data if isinstance(data, dict) else None),
    )


def upsert_propio(q, data: dict) -> int:
    data = {**data, 'source_kind': 'asesor', 'origen': 'Propio', 'in_registro': False,
            'propio_id': str(data.get('propio_id') or data.get('id') or '')}
    return upsert_contact(q, data)


def update_propio_status(q, propio_id, status):
    q("UPDATE crm_contacts SET estatus=%s, estado=%s, updated_at=NOW() WHERE propio_id=%s",
      (status, status, str(propio_id)))


def delete_test(q, test_id: str) -> int:
    """Borra un contacto de prueba (ghl_id o registro_id). Solo ids que empiezan por test_crm_."""
    if not str(test_id).startswith('test_crm_'):
        raise ValueError('solo se pueden borrar ids de prueba test_crm_')
    for tbl in ('crm_visits', 'crm_offers', 'crm_reservations', 'crm_phase_events', 'crm_respuestas', 'crm_assignments'):
        q(f"DELETE FROM {tbl} WHERE ghl_id=%s", (test_id,))
    rows = q(
        "DELETE FROM crm_contacts WHERE ghl_id=%s OR registro_id=%s RETURNING id",
        (test_id, test_id), fetch=True,
    ) or []
    return len(rows)


def apply(q, op: str, data: dict):
    op = (op or '').strip().lower()
    if op == 'rueda':
        return {'ok': True, 'indices': sync_rueda(q, data.get('state') or data)}
    if op == 'assignment':
        pk = record_assignment(q, data)
        if data.get('state'):
            idx = sync_rueda(q, data['state'])
        else:
            idx = None
        return {'ok': True, 'contact_id': pk, 'rueda': idx}
    if op == 'phase':
        upsert_phase(q, data)
        return {'ok': True}
    if op == 'visit':
        insert_visit(q, data)
        return {'ok': True}
    if op == 'offer':
        insert_offer(q, data)
        return {'ok': True}
    if op == 'reservation':
        insert_reservation(q, data)
        return {'ok': True}
    if op == 'propio':
        return {'ok': True, 'contact_id': upsert_propio(q, data)}
    if op == 'propio_status':
        update_propio_status(q, data.get('propio_id') or data.get('id'), data.get('status') or data.get('estatus'))
        return {'ok': True}
    if op == 'delete_test':
        return {'ok': True, 'deleted': delete_test(q, data.get('id') or data.get('ghl_id'))}
    if op == 'estatus_sync':
        import crm as _crm
        url = (data.get('url') or '').strip().rstrip('/')
        key = (data.get('key') or '').strip()
        if not url.startswith('https://') or not key:
            return {'ok': False, 'error': 'url o llave inválida'}
        _crm.set_meta(q, 'estatus_sync', {'url': url, 'key': key})
        return {'ok': True}
    return {'ok': False, 'error': f'op desconocida: {op}'}

