"""CRM propia en Postgres (fase 1). El portal lee estas tablas en lugar del push de la caja.

No altera wa_events ni otras tablas existentes. El corte del 31-oct-2026 se aplica
al servir (en_ejercicio): los anteriores quedan guardados como historia y para
reconocer a quien regresa (ghl_id / teléfono).
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from corte_clientes import en_ejercicio

TZ = ZoneInfo('America/Caracas')

# Nombres del portal que esta capa puede servir desde el CRM.
PORTAL_NOMBRES = (
    'clientes_asignados.json',
    'clientes_telefonos.json',
    'productividad.json',
    'cierres.json',
)

CUSTOM_FIELDS = {
    '1RHCJSTy7Cf57SvTwSDQ': 'zonas_interes',
    '8fG1vDM0Y24CNKXOd0eB': 'subtipo_enlace',
    'ARKbPzOyKszSnppNYDV5': 'presupuesto_usd',
    'CNaXB1UhIjT8sHYhQ5XT': 'estatus_ghl',
    'ELVexBsCdMm5n4CaBEqO': 'tipo_requerimiento',
    'JYL3acieVElLf9ZfFAb8': 'agregado_por_lista',
    'LPtZ1wTnBty8OyPirsjY': 'estado_enlace',
    'MitcAaYHY5hJ89JEiaHQ': 'tipo_de_cliente',
    'MtMeHKCuLbHeiCqdDs0o': 'operacion_ghl',
    'OSxDjPCYVBPowkDzwHAr': 'whatsapp_asesor',
    'SgEl65rNGfeZDotDpnQM': 'propiedad_interes',
    'TOjx2iN0wjQ634Wm1INQ': 'tipo_lead',
    'pPN2n5zwT15AleBdgBCH': 'detalles_requerimiento',
    'waesHUKR15dZcG7Ac5s3': 'agregado_por',
    'zSBhq40pe1LzYdE2APF9': 'asesor_asignado',
}

RUEDA_TIPOS = {
    'busqueda': {'state_key': 'index', 'label': 'Búsqueda'},
    'vpc': {'state_key': 'index_vpc', 'label': 'Visita Punta Cerradora'},
    'captacion': {'state_key': 'index_captacion', 'label': 'Captación'},
    'consulta_pc': {'state_key': 'index_consulta_pc', 'label': 'Consulta Punta Cerradora'},
    'consulta_ml_pc': {'state_key': 'index_consulta_ml_pc', 'label': 'Consulta MercadoLibre Punta Cerradora'},
}


def _j(v):
    if v is None:
        return None
    if isinstance(v, (bytes, memoryview)):
        v = bytes(v).decode()
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _meta(q, key, default=None):
    rows = q('SELECT value FROM crm_meta WHERE key=%s', (key,), fetch=True) or []
    if not rows:
        return default
    return _j(rows[0]['value'])


def set_meta(q, key, value):
    q("""INSERT INTO crm_meta (key, value, updated_at) VALUES (%s, %s, NOW())
         ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()""",
      (key, value))


def estatus_sync_conf(q):
    """URL y llave del Sistema de Clientes. No imprime valores."""
    d = _meta(q, 'estatus_sync', {}) or {}
    return d if isinstance(d, dict) else {}


def _iso_asignado(raw, typed=None):
    """ISO corto (YYYY-MM-DDTHH:MM:SS) desde texto o timestamp."""
    for v in (raw, typed):
        if v is None or v == '':
            continue
        if hasattr(v, 'isoformat'):
            try:
                if getattr(v, 'tzinfo', None) is None:
                    v = v.replace(tzinfo=TZ)
                v = v.astimezone(TZ)
            except Exception:
                pass
            s = v.isoformat()
        else:
            s = str(v).replace(' ', 'T')
        s = s.replace('Z', '')
        return s[:19]
    return ''


def _contactos_portal(q):
    rows = q(
        """SELECT registro_id, ghl_id, propio_id, asesor_slug, name, estatus, estado, situacion,
                  inmueble, operacion, asignado_at_raw, asignado_at, origen, tipo_lead
           FROM crm_contacts
           WHERE NOT excluido AND asesor_slug IS NOT NULL
             AND (in_registro OR asignado_at IS NOT NULL OR asignado_at_raw IS NOT NULL)
           ORDER BY asignado_at NULLS LAST, asignado_at_raw NULLS LAST, id""",
        fetch=True,
    ) or []
    out = []
    for r in rows:
        out.append({
            'id': r.get('registro_id') or r.get('ghl_id') or r.get('propio_id') or '',
            'asesor': r.get('asesor_slug') or '',
            'nombre': r.get('name') or '',
            'estatus': r.get('estatus') or '',
            'estado': r.get('estado') or '',
            'situacion': r.get('situacion') or '',
            'inmueble': r.get('inmueble') or '',
            'operacion': r.get('operacion') or '',
            'asignado_at': _iso_asignado(r.get('asignado_at_raw'), r.get('asignado_at')),
            'origen': r.get('origen') or 'Asignado por For Rent',
            'tipo_lead': r.get('tipo_lead') or '',
        })
    return out


def _telefonos(q):
    rows = q(
        """SELECT registro_id, ghl_id, propio_id, name, asesor_nombre, asignado_at_raw, phone, phone_alt
           FROM crm_contacts
           WHERE in_registro
           ORDER BY asignado_at_raw NULLS LAST, id""",
        fetch=True,
    ) or []
    out = []
    for r in rows:
        kid = r.get('registro_id') or r.get('ghl_id') or r.get('propio_id') or ''
        out.append({
            'id': kid,
            'contact_id': kid,
            'nombre': r.get('name') or '',
            'asesor': r.get('asesor_nombre') or '',
            'asignado_at': r.get('asignado_at_raw') or '',
            'telefono': r.get('phone') or '',
            'telefono_alt': r.get('phone_alt') or '',
        })
    return out


def _productividad(q, clientes):
    from datetime import timedelta
    ej = {c['id']: c for c in clientes if en_ejercicio(asignado_at=c.get('asignado_at'))}
    lim = datetime.now(TZ) - timedelta(days=7)

    def reciente(iso):
        try:
            d = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
            if d.tzinfo is None:
                d = d.replace(tzinfo=TZ)
            return d >= lim
        except Exception:
            return False

    resp_rows = q('SELECT ghl_id, payload FROM crm_respuestas', fetch=True) or []
    resp = {r['ghl_id']: _j(r['payload']) or {} for r in resp_rows}
    reg_rows = q(
        'SELECT registro_id, ghl_id, registro FROM crm_contacts WHERE in_registro',
        fetch=True,
    ) or []
    reg = {}
    for r in reg_rows:
        payload = _j(r['registro']) or {}
        if r.get('registro_id'):
            reg[r['registro_id']] = payload
        if r.get('ghl_id'):
            reg.setdefault(r['ghl_id'], payload)
    por = {}
    for k, c in ej.items():
        a = por.setdefault(c['asesor'], {'clientes': 0, 'contactados': 0, 'seguimientos_7d': 0})
        a['clientes'] += 1
        if c['estatus'] not in ('Sin estatus', 'No contactado', 'Nuevo'):
            a['contactados'] += 1
        r = resp.get(k) or {}
        marcas = [r.get('at')] + [h.get('at') for h in (r.get('historial') or []) if isinstance(h, dict)]
        v = reg.get(k) or {}
        ur = v.get('ultima_respuesta') or {}
        if isinstance(ur, dict):
            marcas.append(ur.get('at'))
        a['seguimientos_7d'] += len({m for m in marcas if m and reciente(m)})
    return por


def _cierres(q):
    rows = q('SELECT payload FROM crm_cierres', fetch=True) or []
    out = []
    for r in rows:
        p = _j(r['payload'])
        if isinstance(p, dict):
            out.append(p)
    return out


def crm_listo(q) -> bool:
    try:
        rows = q("SELECT 1 FROM crm_meta WHERE key='import_ok'", fetch=True) or []
        return bool(rows)
    except Exception:
        return False


def _stamp_vivo(q, meta=None):
    """Sello de actualización en vivo: el cambio más reciente en el CRM, no la fecha de un import."""
    meta = meta or {}
    try:
        rows = q(
            """SELECT GREATEST(
                    COALESCE((SELECT MAX(updated_at) FROM crm_contacts), '-infinity'::timestamptz),
                    COALESCE((SELECT MAX(assigned_at) FROM crm_assignments), '-infinity'::timestamptz)
               ) AS u""",
            fetch=True,
        ) or []
        u = rows[0].get('u') if rows else None
        if u is not None and str(u) not in ('', 'None', '-infinity'):
            iso = _iso_asignado(None, u)
            if iso:
                # Conserva zona Caracas en el sello.
                if hasattr(u, 'isoformat'):
                    if getattr(u, 'tzinfo', None) is None:
                        u = u.replace(tzinfo=TZ)
                    return u.astimezone(TZ).isoformat(timespec='seconds')
                return iso
    except Exception:
        pass
    return datetime.now(TZ).isoformat(timespec='seconds')


def portal_payload(nombre, q):
    """Arma el mismo JSON que el portal ya consume. None si este nombre no sale del CRM o aún no hay carga."""
    if nombre not in PORTAL_NOMBRES or not crm_listo(q):
        return None
    meta = _meta(q, 'import_ok', {}) or {}
    ahora = _stamp_vivo(q, meta)
    if nombre == 'clientes_asignados.json':
        clientes = _contactos_portal(q)
        return {
            'actualizado': ahora,
            'registro_actualizado': meta.get('registro_actualizado') or '',
            'respuestas_en_vivo': True,
            'fuente': 'CRM propia (Postgres) y Sistema de Clientes. Corte 31-oct-2026 al servir.',
            'clientes': clientes,
        }
    if nombre == 'clientes_telefonos.json':
        tels = _telefonos(q)
        return {'fuente': 'CRM propia (Postgres)', 'generado_at': ahora, 'n': len(tels), 'clientes': tels}
    if nombre == 'productividad.json':
        clientes = _contactos_portal(q)
        return {
            'actualizado': ahora,
            'respuestas_en_vivo': True,
            'fuente': 'CRM propia (Postgres), ejercicio desde el corte 31-oct-2026',
            'por_asesor': _productividad(q, clientes),
        }
    if nombre == 'cierres.json':
        return {
            'actualizado': ahora,
            'fuente_modificada': meta.get('cierres_fuente_modificada') or '',
            'fuente': 'CRM propia (Postgres): libro de comisiones + cierres_seguimiento',
            'cierres': _cierres(q),
        }
    return None
