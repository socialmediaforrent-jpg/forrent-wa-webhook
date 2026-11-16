#!/usr/bin/env python3
"""Carga el export GHL y las fuentes de la caja en las tablas crm_*. Idempotente.
No imprime secretos, teléfonos, nombres ni cuerpos de notas. Solo conteos.
"""
from __future__ import annotations

import argparse, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# El import vive en el repo; las fuentes por defecto están en la caja.
ROOT_DEFAULT = Path('/workspace/forrent')
TZ = ZoneInfo('America/Caracas')

# ids de los 15 campos personalizados GHL → columna
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

RUEDAS = [
    ('busqueda', 'index', 'ROUND_ROBIN_NAMES'),
    ('vpc', 'index_vpc', 'VPC_ROUND_ROBIN_NAMES'),
    ('captacion', 'index_captacion', 'CAPTACION_ROUND_ROBIN_NAMES'),
    ('consulta_pc', 'index_consulta_pc', 'CPC_ROUND_ROBIN_NAMES'),
    ('consulta_ml_pc', 'index_consulta_ml_pc', 'CML_PC_ROUND_ROBIN_NAMES'),
]


def log(*a):
    print(*a, flush=True)


def norm_phone(s):
    digits = re.sub(r'\D', '', s or '')
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0') and len(digits) == 11:
        digits = '58' + digits[1:]
    return digits or None


def parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def loadj(path, default=None):
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    if p.suffix == '.jsonl':
        return None  # iter separately
    with p.open(encoding='utf-8') as f:
        return json.load(f)


def iter_jsonl(path):
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def cf_values(cfs):
    out = {col: None for col in CUSTOM_FIELDS.values()}
    raw = {}
    for item in cfs or []:
        if not isinstance(item, dict):
            continue
        fid = item.get('id') or item.get('fieldKey')
        val = item.get('value')
        if fid:
            raw[fid] = val
        col = CUSTOM_FIELDS.get(fid)
        if col:
            if col == 'tipo_de_cliente':
                out[col] = val
            else:
                if isinstance(val, list):
                    out[col] = ', '.join(str(x) for x in val)
                else:
                    out[col] = None if val is None else str(val)
    return out, raw


def slug_map(asesores):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import portal_lib
    m = {}
    for a in asesores:
        for n in [a.get('name'), a.get('user')] + list(a.get('aliases') or []):
            if n:
                m[portal_lib.norm(n)] = a['user']
    return m, portal_lib


def connect(dsn):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn, RealDictCursor


def run_sql(conn, sql_path):
    cur = conn.cursor()
    cur.execute(Path(sql_path).read_text())
    conn.commit()
    cur.close()


def wipe_crm(cur):
    # Solo tablas crm_*; no toca wa_events ni el resto.
    cur.execute("""
        TRUNCATE TABLE
          crm_contact_tags, crm_notes, crm_assignments, crm_phase_events,
          crm_respuestas, crm_visits, crm_offers, crm_reservations,
          crm_cierres, crm_opportunities, crm_contacts, crm_tags,
          crm_rueda_state, crm_meta
        RESTART IDENTITY CASCADE
    """)


def upsert_meta(cur, key, value):
    from psycopg2.extras import Json
    cur.execute(
        """INSERT INTO crm_meta (key, value, updated_at) VALUES (%s, %s, NOW())
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()""",
        (key, Json(value)),
    )


def import_all(conn, root: Path, export: Path, sql_path: Path):
    from psycopg2.extras import Json, RealDictCursor
    cur = conn.cursor()
    run_sql(conn, sql_path)
    cur = conn.cursor()
    wipe_crm(cur)

    asesores = loadj(root / 'wa_webhook_server' / 'data' / 'asesores.json', {}).get('asesores') or []
    slugs, portal_lib = slug_map(asesores)

    # --- tags de ubicación
    tags_doc = loadj(export / 'tags_location.json', {}) or {}
    tags = tags_doc.get('tags') or []
    tag_id_by_name = {}
    tag_id_by_ghl = {}
    n_tags = 0
    for t in tags:
        name = (t.get('name') or '').strip()
        if not name:
            continue
        cur.execute(
            """INSERT INTO crm_tags (ghl_id, name, color, category_id)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (name) DO UPDATE SET ghl_id=COALESCE(EXCLUDED.ghl_id, crm_tags.ghl_id),
                 color=EXCLUDED.color
               RETURNING id""",
            (t.get('id'), name, t.get('color'), t.get('categoryId')),
        )
        tid = cur.fetchone()[0]
        tag_id_by_name[name] = tid
        if t.get('id'):
            tag_id_by_ghl[t['id']] = tid
        n_tags += 1
    log('tags ubicación', n_tags)

    def get_tag(name):
        name = (name or '').strip()
        if not name:
            return None
        if name in tag_id_by_name:
            return tag_id_by_name[name]
        cur.execute(
            "INSERT INTO crm_tags (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (name,),
        )
        tid = cur.fetchone()[0]
        tag_id_by_name[name] = tid
        return tid

    # --- contactos GHL
    n_contacts = 0
    n_tag_links = 0
    contact_pk = {}
    phone_pk = {}
    for o in iter_jsonl(export / 'contacts.jsonl'):
        if 'contact' in o and 'id' not in o:
            o = o['contact']
        gid = o.get('id')
        if not gid:
            continue
        cols, raw_cf = cf_values(o.get('customFields'))
        phone = o.get('phone') or ''
        pn = norm_phone(phone)
        name = o.get('contactName') or ' '.join(x for x in (o.get('firstName'), o.get('lastName')) if x).strip()
        cur.execute(
            """INSERT INTO crm_contacts (
                 ghl_id, phone, phone_normalized, email, first_name, last_name, name,
                 source, source_kind, country, city, state, address1, postal_code,
                 date_added, date_updated, assigned_to_ghl,
                 zonas_interes, subtipo_enlace, presupuesto_usd, estatus_ghl, tipo_requerimiento,
                 agregado_por_lista, estado_enlace, tipo_de_cliente, operacion_ghl, whatsapp_asesor,
                 propiedad_interes, tipo_lead, detalles_requerimiento, agregado_por, asesor_asignado,
                 custom_fields, asesor_nombre, created_at, updated_at
               ) VALUES (
                 %(ghl_id)s, %(phone)s, %(phone_normalized)s, %(email)s, %(first_name)s, %(last_name)s, %(name)s,
                 %(source)s, 'ghl', %(country)s, %(city)s, %(state)s, %(address1)s, %(postal_code)s,
                 %(date_added)s, %(date_updated)s, %(assigned_to_ghl)s,
                 %(zonas_interes)s, %(subtipo_enlace)s, %(presupuesto_usd)s, %(estatus_ghl)s, %(tipo_requerimiento)s,
                 %(agregado_por_lista)s, %(estado_enlace)s, %(tipo_de_cliente)s, %(operacion_ghl)s, %(whatsapp_asesor)s,
                 %(propiedad_interes)s, %(tipo_lead)s, %(detalles_requerimiento)s, %(agregado_por)s, %(asesor_asignado)s,
                 %(custom_fields)s, %(asesor_nombre)s, NOW(), NOW()
               )
               ON CONFLICT (ghl_id) DO UPDATE SET
                 phone=EXCLUDED.phone, phone_normalized=EXCLUDED.phone_normalized, name=EXCLUDED.name,
                 custom_fields=EXCLUDED.custom_fields, updated_at=NOW()
               RETURNING id""",
            {
                'ghl_id': gid, 'phone': phone or None, 'phone_normalized': pn,
                'email': o.get('email'), 'first_name': o.get('firstName'), 'last_name': o.get('lastName'),
                'name': name, 'source': o.get('source'),
                'country': o.get('country'), 'city': o.get('city'), 'state': o.get('state'),
                'address1': o.get('address1'), 'postal_code': o.get('postalCode'),
                'date_added': parse_dt(o.get('dateAdded')), 'date_updated': parse_dt(o.get('dateUpdated')),
                'assigned_to_ghl': o.get('assignedTo'),
                **{k: (Json(v) if k == 'tipo_de_cliente' else v) for k, v in cols.items()},
                'custom_fields': Json(raw_cf),
                'asesor_nombre': cols.get('asesor_asignado'),
            },
        )
        pk = cur.fetchone()[0]
        contact_pk[gid] = pk
        if pn:
            phone_pk.setdefault(pn, pk)
        for tag in o.get('tags') or []:
            tname = tag if isinstance(tag, str) else (tag.get('name') if isinstance(tag, dict) else None)
            tid = get_tag(tname)
            if tid:
                cur.execute(
                    "INSERT INTO crm_contact_tags (contact_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (pk, tid),
                )
                n_tag_links += 1
        n_contacts += 1
    log('contactos GHL', n_contacts, 'enlaces tag', n_tag_links)

    # --- notas
    n_notes = n_notes_orphan = 0
    notes_path = export / 'notes.jsonl'
    if notes_path.exists():
        for o in iter_jsonl(notes_path):
            gid = o.get('contactId')
            cur.execute(
                """INSERT INTO crm_notes (contact_id, ghl_id, ghl_contact_id, body, created_by, pinned, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (ghl_id) DO NOTHING""",
                (contact_pk.get(gid), o.get('id'), gid, o.get('body') or o.get('bodyText'),
                 o.get('userId'), bool(o.get('pinned')), parse_dt(o.get('dateAdded'))),
            )
            n_notes += 1
            if gid not in contact_pk:
                n_notes_orphan += 1
    log('notas', n_notes, 'sin contacto', n_notes_orphan)

    # --- oportunidades
    n_opp = 0
    opps = loadj(export / 'opportunities.json', [])
    if isinstance(opps, dict):
        opps = opps.get('opportunities') or opps.get('data') or []
    pipes = {p.get('id'): p for p in (loadj(export / 'pipelines.json', {}).get('pipelines') or [])}
    stages = {}
    for p in pipes.values():
        for s in p.get('stages') or []:
            stages[s.get('id')] = (p.get('name'), s.get('name'))
    for o in opps or []:
        if not isinstance(o, dict):
            continue
        cid = o.get('contactId') or (o.get('contact') or {}).get('id')
        st = stages.get(o.get('pipelineStageId') or o.get('stageId'), (None, None))
        cur.execute(
            """INSERT INTO crm_opportunities (ghl_id, contact_id, pipeline_id, pipeline_name, stage_id, stage_name,
                   name, status, monetary_value, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (ghl_id) DO NOTHING""",
            (o.get('id'), contact_pk.get(cid), o.get('pipelineId'), st[0] or o.get('pipelineName'),
             o.get('pipelineStageId') or o.get('stageId'), st[1], o.get('name'), o.get('status'),
             o.get('monetaryValue') or o.get('value'), Json(o)),
        )
        n_opp += 1
    log('oportunidades', n_opp)

    # --- registro + respuestas (portal)
    sys.path.insert(0, str(root / 'scripts'))
    import portal_build
    resp_raw, resp_ok = portal_build.respuestas_vivas()
    clientes_portal, reg_act = portal_build.build_asignados(resp_raw)
    tels = portal_build.build_telefonos()
    tel_by_id = {t.get('id'): t for t in tels}
    cierres, ci_mt = portal_build.build_cierres()

    cfg = loadj(root / 'seguimiento_tg' / 'config.json', {}) or {}
    excluir = set(cfg.get('excluir_contactos') or [])
    reg = loadj(root / 'seguimiento_tg' / 'registro.json', {}) or {}
    reg_cli = reg.get('clientes') or {}

    n_reg = n_new = 0
    for k, v in reg_cli.items():
        if not isinstance(v, dict):
            continue
        gid = v.get('contact_id') or k
        # El id del portal es la clave del registro (k). No fusionar por teléfono
        # cuando k no es un ghl_id ya cargado: si no, se pierde esa fila en los conteos.
        pk = contact_pk.get(k) or contact_pk.get(gid) if k == gid else contact_pk.get(k)
        if pk is None and k == gid:
            pk = contact_pk.get(gid)
        pn = norm_phone(v.get('telefono') or '')
        slug = slugs.get(portal_lib.norm(v.get('asesor') or ''))
        aa_raw = v.get('asignado_at') or ''
        tel_row = tel_by_id.get(k) or tel_by_id.get(gid) or {}
        phone = v.get('telefono') or tel_row.get('telefono') or None
        if pk is None:
            cur.execute(
                """INSERT INTO crm_contacts (
                     ghl_id, registro_id, phone, phone_normalized, phone_alt, name, source_kind,
                     tipo_lead, asesor_slug, asesor_nombre, asignado_at_raw, asignado_at,
                     inmueble, operacion, origen, tg_estado, in_registro, excluido,
                     registro, created_at, updated_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,'registro',%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,NOW(),NOW())
                   RETURNING id""",
                (gid if gid in contact_pk else None, k, phone, pn, v.get('telefono_alt') or tel_row.get('telefono_alt'),
                 v.get('nombre') or '', v.get('tipo_lead'), slug, v.get('asesor'),
                 aa_raw, parse_dt(aa_raw), v.get('inmueble'), v.get('operacion'),
                 v.get('origen'), v.get('estado'), k in excluir, Json(v)),
            )
            pk = cur.fetchone()[0]
            if gid:
                contact_pk[gid] = pk
            if pn:
                phone_pk.setdefault(pn, pk)
            n_new += 1
        else:
            cur.execute(
                """UPDATE crm_contacts SET
                     in_registro=TRUE,
                     excluido=%s,
                     asesor_slug=COALESCE(%s, asesor_slug),
                     asesor_nombre=COALESCE(%s, asesor_nombre),
                     asignado_at_raw=%s,
                     asignado_at=%s,
                     inmueble=COALESCE(%s, inmueble),
                     operacion=COALESCE(%s, operacion),
                     origen=COALESCE(%s, origen),
                     tipo_lead=COALESCE(%s, tipo_lead),
                     tg_estado=%s,
                     phone=COALESCE(phone, %s),
                     phone_normalized=COALESCE(phone_normalized, %s),
                     phone_alt=COALESCE(%s, phone_alt),
                     name=COALESCE(NULLIF(name,''), %s),
                     registro=%s,
                     updated_at=NOW()
                   WHERE id=%s""",
                (k in excluir, slug, v.get('asesor'), aa_raw, parse_dt(aa_raw),
                 v.get('inmueble'), v.get('operacion') if v.get('operacion') not in (None, '', 'sin_tipo') else None,
                 v.get('origen'), v.get('tipo_lead'), v.get('estado'),
                 phone, pn, v.get('telefono_alt') or tel_row.get('telefono_alt'),
                 v.get('nombre') or '', Json(v), pk),
            )
        # visitas / ofertas del registro
        if v.get('visita'):
            cur.execute(
                """INSERT INTO crm_visits (contact_id, ghl_id, asesor_slug, cliente, inmueble, resultado, source, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,'registro',%s)""",
                (pk, gid, slug, v.get('nombre'), v.get('inmueble'), v.get('resultado_visita'), Json(v.get('visita'))),
            )
        if v.get('ofertas'):
            cur.execute(
                """INSERT INTO crm_offers (contact_id, ghl_id, asesor_slug, cliente, inmueble, monto, moneda, source, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'registro',%s)""",
                (pk, gid, slug, v.get('nombre'), v.get('inmueble'), v.get('monto_oferta'), v.get('moneda_oferta'), Json(v.get('ofertas'))),
            )
        # asignación inicial del registro
        if v.get('asesor') or aa_raw:
            cur.execute(
                """INSERT INTO crm_assignments (contact_id, ghl_id, asesor_nombre, asesor_slug, tipo_lead, assigned_at, source)
                   VALUES (%s,%s,%s,%s,%s,%s,'registro')""",
                (pk, gid if gid != k else (gid if contact_pk.get(gid)==pk else None), v.get('asesor'), slug, v.get('tipo_lead'), parse_dt(aa_raw)),
            )
        cur.execute("UPDATE crm_contacts SET registro_id=COALESCE(registro_id, %s) WHERE id=%s AND (registro_id IS NULL OR registro_id=%s)", (k, pk, k))
        contact_pk[k] = pk
        if gid:
            contact_pk.setdefault(gid, pk)
        n_reg += 1
    log('registro clientes', n_reg, 'altas solo-registro', n_new)

    # portal fields (estado/situacion/estatus) — misma lógica que portal_build
    n_portal = 0
    for c in clientes_portal:
        gid = c.get('id')
        pk = contact_pk.get(gid)
        if not pk:
            continue
        cur.execute(
            """UPDATE crm_contacts SET
                 asesor_slug=%s, estado=%s, situacion=%s, estatus=%s,
                 inmueble=COALESCE(%s, inmueble), operacion=COALESCE(NULLIF(%s,''), operacion),
                 origen=%s, in_registro=TRUE, updated_at=NOW()
               WHERE id=%s""",
            (c.get('asesor'), c.get('estado'), c.get('situacion'), c.get('estatus'),
             c.get('inmueble'), c.get('operacion'), c.get('origen') or 'Asignado por For Rent', pk),
        )
        n_portal += 1
    log('filas portal asignados', n_portal)

    # respuestas
    n_resp = 0
    for k, payload in (portal_build.est.mig(resp_raw) or resp_raw or {}).items():
        if not isinstance(payload, dict):
            continue
        pk = contact_pk.get(k)
        cur.execute(
            """INSERT INTO crm_respuestas (ghl_id, contact_id, payload, updated_at)
               VALUES (%s,%s,%s,NOW())
               ON CONFLICT (ghl_id) DO UPDATE SET payload=EXCLUDED.payload, contact_id=COALESCE(EXCLUDED.contact_id, crm_respuestas.contact_id), updated_at=NOW()""",
            (k, pk, Json(payload)),
        )
        at = parse_dt(payload.get('at'))
        cur.execute(
            """INSERT INTO crm_phase_events (contact_id, ghl_id, estado, situacion, estatus, at, source, payload)
               VALUES (%s,%s,%s,%s,%s,%s,'respuestas',%s)""",
            (pk, k, payload.get('estado'), payload.get('situacion'), payload.get('estado'), at, Json(payload)),
        )
        hist = payload.get('historial') or []
        for h in hist:
            if isinstance(h, dict):
                cur.execute(
                    """INSERT INTO crm_phase_events (contact_id, ghl_id, estado, situacion, at, source, payload)
                       VALUES (%s,%s,%s,%s,%s,'respuestas_historial',%s)""",
                    (pk, k, h.get('estado'), h.get('situacion'), parse_dt(h.get('at')), Json(h)),
                )
        n_resp += 1
    log('respuestas', n_resp, 'vivas' if resp_ok else 'locales')

    # --- rueda
    rr = loadj(root / 'reports' / 'round_robin_state.json', {}) or {}
    try:
        sys.path.insert(0, str(root / 'scripts'))
        import assign_lead as al
        ordenes = {
            'ROUND_ROBIN_NAMES': list(al.ROUND_ROBIN_NAMES),
            'VPC_ROUND_ROBIN_NAMES': list(al.VPC_ROUND_ROBIN_NAMES),
            'CAPTACION_ROUND_ROBIN_NAMES': list(al.CAPTACION_ROUND_ROBIN_NAMES),
            'CPC_ROUND_ROBIN_NAMES': list(al.CPC_ROUND_ROBIN_NAMES),
            'CML_PC_ROUND_ROBIN_NAMES': list(al.CML_PC_ROUND_ROBIN_NAMES),
        }
        pausados = {'PAUSADOS': sorted(al.PAUSADOS), 'PAUSADOS_PC': sorted(al.PAUSADOS_PC)}
    except Exception as e:
        log('aviso: no se pudo leer assign_lead', type(e).__name__)
        ordenes, pausados = {}, {}
    notas_rueda = {k: v for k, v in rr.items() if k.startswith('note') or k.startswith('nota')}
    for tipo, key, orden_name in RUEDAS:
        cur.execute(
            """INSERT INTO crm_rueda_state (tipo, state_key, indice, orden, pausados, updated_at, notas, raw)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tipo, key, int(rr.get(key) or 0), Json(ordenes.get(orden_name) or []),
             Json(pausados), parse_dt(str(rr.get('updated_at') or '')) if False else None,
             Json(notas_rueda), Json(rr)),
        )
    log('rueda tipos', len(RUEDAS), 'índices', {t: rr.get(k) for t, k, _ in RUEDAS})

    # --- historial poll_assign
    ps = loadj(root / 'reports' / 'poll_assign_state.json', {}) or {}
    n_asig = 0
    for gid, info in (ps.get('assigned') or {}).items():
        if not isinstance(info, dict):
            continue
        slug = slugs.get(portal_lib.norm(info.get('asesor') or ''))
        cur.execute(
            """INSERT INTO crm_assignments (contact_id, ghl_id, asesor_nombre, asesor_slug, tipo_lead, assigned_at, source, note, payload)
               VALUES (%s,%s,%s,%s,%s,%s,'poll_assign',%s,%s)""",
            (contact_pk.get(gid), gid, info.get('asesor'), slug, info.get('tipo'),
             parse_dt(info.get('assigned_at')), info.get('note'), Json(info)),
        )
        n_asig += 1
    log('asignaciones poll_assign', n_asig)

    # --- cierres / reservas
    n_ci = 0
    for c in cierres:
        cur.execute(
            """INSERT INTO crm_cierres (id, asesor_slug, fecha, operacion, inmueble, codigo, origen, estado_pago, cliente, fuente, calc, prueba, payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO NOTHING""",
            (c.get('id'), c.get('asesor'), c.get('fecha'), c.get('operacion'), c.get('inmueble'),
             c.get('codigo'), c.get('origen'), c.get('estado_pago'), c.get('cliente'), c.get('fuente'),
             Json(c.get('calc') or {}), bool(c.get('prueba')), Json(c)),
        )
        n_ci += 1
    seg = loadj(root / 'cierres_seguimiento.json', {}) or {}
    n_res = 0
    for x in seg.get('reservas') or []:
        slug = slugs.get(portal_lib.norm(x.get('asesor') or ''))
        cur.execute(
            """INSERT INTO crm_reservations (ghl_id, asesor_slug, asesor_nombre, fecha, operacion, inmueble, codigo, wasi, origen, estado, cliente, comision, source, payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'cierres_seguimiento',%s)""",
            (x.get('id'), slug, x.get('asesor'), x.get('fecha_firma') or x.get('fecha') or x.get('fecha_reserva'),
             x.get('tipo'), x.get('inmueble'), str(x.get('codigo') or ''), str(x.get('wasi') or ''),
             x.get('origen'), x.get('estado'), x.get('arrendatario') or x.get('cliente'),
             Json(x.get('comision') if isinstance(x.get('comision'), dict) else {}), Json(x)),
        )
        n_res += 1
    log('cierres', n_ci, 'reservas', n_res)

    # --- clientes propios y visitas/ofertas ya en Postgres (tablas existentes, solo lectura)
    n_propios = n_vis = n_of = 0
    try:
        cur.execute('SELECT * FROM asesor_clientes')
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            pn = norm_phone(r.get('telefono') or '')
            pk = phone_pk.get(pn) if pn else None
            if pk is None:
                cur.execute(
                    """INSERT INTO crm_contacts (
                         phone, phone_normalized, name, source_kind, asesor_slug,
                         estatus, estado, origen, propio_id, created_at, updated_at, in_registro
                       ) VALUES (%s,%s,%s,'asesor',%s,%s,%s,'Propio',%s,%s,NOW(),FALSE)
                       RETURNING id""",
                    (r.get('telefono'), pn, r.get('nombre'), r.get('asesor_slug'),
                     r.get('status'), r.get('status'), str(r.get('id')), parse_dt(r.get('created_at'))),
                )
                pk = cur.fetchone()[0]
                if pn:
                    phone_pk[pn] = pk
                n_propios += 1
            else:
                cur.execute(
                    """UPDATE crm_contacts SET propio_id=COALESCE(propio_id, %s),
                         source_kind=CASE WHEN source_kind='ghl' AND in_registro THEN source_kind ELSE
                           CASE WHEN in_registro THEN source_kind ELSE 'asesor' END END,
                         updated_at=NOW()
                       WHERE id=%s""",
                    (str(r.get('id')), pk),
                )
                n_propios += 1
    except Exception as e:
        log('aviso: asesor_clientes', type(e).__name__)
        conn.rollback()
        # re-open transaction if needed — caller uses one transaction; re-raise after log? 
        # If the table read failed mid-transaction after previous inserts, rollback would wipe import.
        # Better: asesor_clientes query in the same transaction should work. If it fails, raise.
        raise

    try:
        cur.execute('SELECT * FROM asesor_visitas')
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            cur.execute(
                """INSERT INTO crm_visits (asesor_slug, fecha, hora, cliente, inmueble, nota, created_at, source, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'asesor_visitas',%s)""",
                (r.get('asesor_slug'), r.get('fecha'), r.get('hora'), r.get('cliente'), r.get('inmueble'),
                 r.get('nota'), parse_dt(r.get('created_at')), Json({k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in r.items()})),
            )
            n_vis += 1
        cur.execute('SELECT * FROM asesor_ofertas')
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            cur.execute(
                """INSERT INTO crm_offers (asesor_slug, tipo, cliente, inmueble, monto, fecha, nota, created_at, source, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'asesor_ofertas',%s)""",
                (r.get('asesor_slug'), r.get('tipo'), r.get('cliente'), r.get('inmueble'), r.get('monto'),
                 r.get('fecha'), r.get('nota'), parse_dt(r.get('created_at')),
                 Json({k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in r.items()})),
            )
            n_of += 1
    except Exception as e:
        log('aviso: visitas/ofertas', type(e).__name__)
        raise
    log('propios copiados', n_propios, 'visitas asesor', n_vis, 'ofertas asesor', n_of)

    ahora = datetime.now(TZ).isoformat(timespec='seconds')
    upsert_meta(cur, 'import_ok', {
        'actualizado': ahora,
        'registro_actualizado': reg_act,
        'respuestas_en_vivo': resp_ok,
        'cierres_fuente_modificada': ci_mt,
        'conteos': {
            'contactos_ghl': n_contacts,
            'tags': n_tags,
            'notas': n_notes,
            'registro': n_reg,
            'portal_asignados': n_portal,
            'respuestas': n_resp,
            'asignaciones_poll': n_asig,
            'cierres': n_ci,
            'reservas': n_res,
            'propios': n_propios,
            'visitas_asesor': n_vis,
            'ofertas_asesor': n_of,
            'oportunidades': n_opp,
        },
        'corte': '2026-10-31',
    })
    conn.commit()
    log('import_ok', ahora)
    return True


def dsn_from_render():
    """Lee la URL externa, añade hostaddr (DoH) y sslmode. No imprime la URL."""
    key = Path('/workspace/forrent/secrets/render_api_key.txt').read_text().strip()
    db = 'dpg-dbsu11s9v7es73blbvdg-a'
    req = urllib.request.Request(
        f'https://api.render.com/v1/postgres/{db}/connection-info',
        headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        ci = json.loads(r.read())
    info = ci.get('connectionInfo') if 'connectionInfo' in ci else ci
    url = info.get('externalConnectionString')
    u = urllib.parse.urlparse(url)
    host = u.hostname
    real_ip = None
    for doh in (
        'https://cloudflare-dns.com/dns-query?name=' + host + '&type=A',
        'https://dns.google/resolve?name=' + host + '&type=A',
    ):
        try:
            q = urllib.request.Request(doh, headers={'Accept': 'application/dns-json'})
            with urllib.request.urlopen(q, timeout=15) as r:
                ans = json.loads(r.read())
            for a in ans.get('Answer') or []:
                if a.get('type') == 1 and a.get('data'):
                    real_ip = a['data']
                    break
            if real_ip:
                break
        except Exception:
            continue
    if not real_ip:
        raise SystemExit('no se resolvió el host de Postgres')
    sep = '&' if '?' in url else '?'
    return url + sep + 'hostaddr=' + real_ip + '&sslmode=require', host, real_ip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=str(ROOT_DEFAULT))
    ap.add_argument('--export', default='')
    ap.add_argument('--sql', default='')
    args = ap.parse_args()
    root = Path(args.root)
    export = Path(args.export or (root / 'backups' / 'ghl_export_20261115'))
    sql_path = Path(args.sql or (Path(__file__).resolve().parent / 'migrations' / '001_crm_propia.sql'))
    dsn, host, ip = dsn_from_render()
    log('pg host', host, 'via', ip)
    import psycopg2  # noqa: F401  (fail early)
    conn, _ = connect(dsn)
    try:
        import_all(conn, root, export, sql_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
