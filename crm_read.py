"""Lecturas del CRM propio (tablas crm_* y wa_events) con la forma que esperan los scripts de la caja.

Reemplaza los GET a GoHighLevel (contacts/ y contacts/{id}) sin cambiar los scripts que los consumen:
cada contacto sale con id, firstName, lastName, contactName, phone, tags, dateAdded, source y
customFields [{id, value}] usando los MISMOS ids de campo de GHL (ver crm.CUSTOM_FIELDS).
Solo lectura. No inventa datos: si un campo no existe en Postgres, sale vacío.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

try:
    from crm import CUSTOM_FIELDS
except Exception:  # pragma: no cover
    CUSTOM_FIELDS = {}

COL_A_CAMPO = {col: fid for fid, col in CUSTOM_FIELDS.items()}
LIMITE = 5000


def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return str(v)


def _txt(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _shape(r, tags):
    cid = r.get('ghl_id') or r.get('registro_id') or f"crm:{r['id']}"
    cf = []
    for col, fid in COL_A_CAMPO.items():
        val = r.get(col)
        if col == 'asesor_asignado':
            val = val or r.get('asesor_nombre')  # escrituras vivas guardan asesor_nombre
        elif col == 'tipo_lead':
            val = val or None
        elif col == 'propiedad_interes':
            val = val or r.get('inmueble')
        if val not in (None, ''):
            cf.append({'id': fid, 'value': _txt(val)})
    return {
        'id': cid,
        'crm_id': r['id'],
        'firstName': r.get('first_name') or '',
        'lastName': r.get('last_name') or '',
        'contactName': r.get('name') or '',
        'name': r.get('name') or '',
        'phone': r.get('phone') or '',
        'email': r.get('email') or '',
        'source': r.get('source') or '',
        'tags': tags.get(r['id'], []),
        'dateAdded': _iso(r.get('date_added') or r.get('created_at')),
        'dateUpdated': _iso(r.get('date_updated') or r.get('updated_at')),
        'customFields': cf,
        'asesor_slug': r.get('asesor_slug') or '',
        'asignado_at': _iso(r.get('asignado_at')),
        'excluido': bool(r.get('excluido')),
        'fuente': 'crm',
    }


def _tags(q, ids):
    if not ids:
        return {}
    rows = q("""SELECT ct.contact_id, t.name FROM crm_contact_tags ct JOIN crm_tags t ON t.id = ct.tag_id
                WHERE ct.contact_id = ANY(string_to_array(%s, ',')::bigint[])""",
             (','.join(str(int(i)) for i in ids),), fetch=True) or []
    out = {}
    for x in rows:
        out.setdefault(x['contact_id'], []).append(x['name'])
    return out


def contactos(q, desde=None, hasta=None, actualizados_desde=None, limite=LIMITE):
    """Contactos del CRM. desde/hasta filtran por fecha de alta (date_added, o created_at si falta)."""
    where, args = [], []
    if desde:
        where.append('COALESCE(date_added, created_at) >= %s'); args.append(desde)
    if hasta:
        where.append('COALESCE(date_added, created_at) < %s'); args.append(hasta)
    if actualizados_desde:
        where.append('COALESCE(updated_at, date_updated, created_at) >= %s'); args.append(actualizados_desde)
    sql = 'SELECT * FROM crm_contacts' + (' WHERE ' + ' AND '.join(where) if where else '')
    sql += ' ORDER BY COALESCE(date_added, created_at) DESC NULLS LAST LIMIT %s'
    args.append(int(min(max(int(limite or LIMITE), 1), LIMITE)))
    rows = q(sql, tuple(args), fetch=True) or []
    tags = _tags(q, [r['id'] for r in rows])
    return [_shape(r, tags) for r in rows]


def contacto(q, cid):
    cid = (cid or '').strip()
    if not cid:
        return None
    if cid.startswith('crm:') and cid[4:].isdigit():
        rows = q('SELECT * FROM crm_contacts WHERE id=%s', (int(cid[4:]),), fetch=True) or []
    else:
        rows = q('SELECT * FROM crm_contacts WHERE ghl_id=%s OR registro_id=%s LIMIT 1', (cid, cid), fetch=True) or []
    if not rows:
        return None
    return _shape(rows[0], _tags(q, [rows[0]['id']]))


def asignaciones(q, desde, hasta=None):
    args = [desde]
    sql = """SELECT a.ghl_id, a.asesor_nombre, a.asesor_slug, a.tipo_lead, a.assigned_at, a.source, a.rueda_tipo,
                    a.note, c.name, c.phone, c.inmueble
             FROM crm_assignments a LEFT JOIN crm_contacts c ON c.id = a.contact_id
             WHERE a.assigned_at >= %s"""
    if hasta:
        sql += ' AND a.assigned_at < %s'; args.append(hasta)
    sql += ' ORDER BY a.assigned_at'
    rows = q(sql, tuple(args), fetch=True) or []
    return [{**r, 'assigned_at': _iso(r.get('assigned_at'))} for r in rows]


def wa_nuevos(q, desde, hasta):
    """Números que escribieron por PRIMERA VEZ por WhatsApp Cloud API (webhook propio) en el rango.
    Solo eventos vivos (no la importación de GHL). Vacío hasta que el número pase a Meta."""
    rows = q("""SELECT wa_id, MIN(ts) AS primero, MAX(name) AS name,
                       BOOL_OR(COALESCE((data::jsonb) ? 'referral', FALSE)) AS referral FROM wa_events
                WHERE kind='message' AND direction='inbound' AND (source IS NULL OR source <> 'ghl_import')
                GROUP BY wa_id HAVING MIN(ts) >= %s AND MIN(ts) < %s""", (desde, hasta), fetch=True) or []
    return [{'wa_id': r['wa_id'], 'primero': _iso(r['primero']), 'name': r.get('name') or '',
             'referral': bool(r.get('referral'))} for r in rows]
