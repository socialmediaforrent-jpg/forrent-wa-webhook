"""Carga única de inmuebles (PRUEBA): la asesora carga una vez; Inventario revisa, aprueba y publica.

Tablas nuevas y aditivas (CREATE TABLE IF NOT EXISTS; no toca tablas existentes):
- carga_inmuebles: ficha (datos públicos y privados por separado), estado y payload Wasi.
- carga_fotos: fotos pendientes guardadas en la base (BYTEA). Nunca se borran: «quitar» = activa=0.
- carga_log: bitácora (quién, cuándo, qué).

Reglas (José, 16-nov-2026):
- Solo propias o alianzas con acuerdo. Dirección, propietario y notas internas NUNCA salen a portales, feeds ni kits.
- Escritura en Wasi detrás de WASI_WRITE_MODE: por defecto «dry-run» (arma el payload exacto y lo registra, no escribe).
- Nada se borra: devolver/rechazar dejan rastro.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from xml.sax.saxutils import escape

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

ESTADOS = {'borrador': 'Borrador', 'enviada': 'Por revisar', 'devuelta': 'Devuelta', 'rechazada': 'Rechazada',
           'aprobada': 'Aprobada'}
OPERACIONES = ('Alquiler', 'Venta')
# tipo portal -> (id_property_type Wasi, property_type Agentiz, residencial)
TIPOS = {
    'Apartamento': (2, 'life_housing_apartment', True), 'Casa': (1, 'life_housing_house', True),
    'Quinta': (1, 'life_housing_house', True), 'Townhouse': (1, 'life_housing_terraced', True),
    'PH': (33, 'life_housing_penthouse', True), 'Dúplex': (20, 'life_housing_duplex', True),
    'Anexo': (14, 'life_housing_apartment', True), 'Local': (3, 'business_buildings_retail', False),
    'Oficina': (4, 'business_buildings_office', False), 'Consultorio': (15, 'business_buildings_medical', False),
    'Galpón': (23, 'business_buildings_industrial', False), 'Terreno': (32, 'business_land_na', False),
    'Edificio': (16, 'business_buildings_na', False),
}
MUNICIPIOS = ('Libertador', 'Chacao', 'Baruta', 'Sucre', 'El Hatillo')   # zonas permitidas (regla de For Rent)
ESTADO_FISICO = {'Nuevo': 1, 'Usado': 2, 'Proyecto': 3, 'En construcción': 4}
FOTOS_MIN, FOTOS_MAX = 6, 20
FOTO_MAX_BYTES = 8 * 1024 * 1024
FOTOS_TOTAL_MAX = 80 * 1024 * 1024
MIME = {b'\xff\xd8\xff': 'image/jpeg', b'\x89PNG\r\n\x1a\n': 'image/png'}
# Portales aliados de Wasi conectados hoy (API portal/all, 16-nov-2026). 43 = Mercado Libre (consume cupo Plata).
PORTALES_WASI = {43: 'Mercado Libre', 75: 'Anuto', 1: 'Trovit', 6: 'Mitula', 12: 'Clasf', 32: 'Los compradores',
                 36: 'Luxury estate', 72: 'Catálogo de Facebook'}
PORTALES_WASI_DEFECTO = (75, 1, 6, 12, 32, 36)     # ML (43) y Catálogo FB (72) solo si Inventario los marca
PORTALES_MANUALES = ('encuadras', 'zonaven', 'housy', 'bienesonline', 'rex')   # Propiedash importa desde Wasi
LARGO_TITULO = {'encuadras': 80, 'zonaven': 90, 'housy': 80, 'bienesonline': 70, 'rex': 80, 'propiedash': 90}

CAMPOS_PUB = ('operacion', 'tipo', 'titulo', 'precio', 'condominio', 'estado_fisico', 'origen', 'aliado', 'municipio', 'zona',
              'habitaciones', 'banos', 'estacionamientos', 'area_construida', 'area_privada', 'area_terreno', 'piso', 'anio',
              'mascotas', 'depositos', 'descripcion', 'video')
CAMPOS_PRIV = ('direccion', 'lat', 'lng', 'propietario_nombre', 'propietario_tel', 'nota_interna')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')).lower() if unicodedata.category(c) != 'Mn').strip()


def catalogo():
    try:
        with open(os.path.join(DATA, 'wasi_catalogos.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'zonas': {}, 'tipos': {}, 'id_currency_usd': 3}


def usuarios_wasi():
    try:
        with open(os.path.join(DATA, 'wasi_usuarios.json'), encoding='utf-8') as f:
            return json.load(f).get('id_user') or {}
    except Exception:
        return {}


# ------------------------------------------------------------------ base de datos
def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    blob = 'BYTEA' if is_pg else 'BLOB'
    q(f"""CREATE TABLE IF NOT EXISTS carga_inmuebles (
        id {serial}, asesor_slug TEXT NOT NULL, estado TEXT NOT NULL, datos TEXT NOT NULL DEFAULT '{{}}',
        privado TEXT NOT NULL DEFAULT '{{}}', paso INTEGER NOT NULL DEFAULT 1,
        revisado_por TEXT, revisado_at TEXT, nota_revision TEXT,
        wasi_modo TEXT, wasi_payload TEXT, wasi_id TEXT, wasi_resultado TEXT, portales_wasi TEXT,
        social_post_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, enviada_at TEXT)""")
    q(f"""CREATE TABLE IF NOT EXISTS carga_fotos (
        id {serial}, carga_id INTEGER NOT NULL, pos INTEGER NOT NULL DEFAULT 0, mime TEXT NOT NULL, tam INTEGER NOT NULL,
        sha256 TEXT NOT NULL, nombre TEXT, datos {blob} NOT NULL, activa INTEGER NOT NULL DEFAULT 1,
        wasi_url TEXT, subido_por TEXT, created_at TEXT NOT NULL)""")
    q('CREATE INDEX IF NOT EXISTS carga_fotos_carga ON carga_fotos (carga_id)')
    q(f"""CREATE TABLE IF NOT EXISTS carga_log (
        id {serial}, carga_id INTEGER NOT NULL, evento TEXT NOT NULL, por TEXT, detalle TEXT, at TEXT NOT NULL)""")


def log_evento(q, cid, evento, por, detalle=None):
    q('INSERT INTO carga_log (carga_id, evento, por, detalle, at) VALUES (%s,%s,%s,%s,%s)',
      (cid, evento, por, json.dumps(detalle, ensure_ascii=False) if detalle is not None else None, now_iso()))


def _row(r):
    if not r:
        return None
    d = dict(r)
    for k in ('datos', 'privado', 'wasi_payload', 'wasi_resultado', 'portales_wasi'):
        v = d.get(k)
        if isinstance(v, str) and v:
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    d['datos'] = d.get('datos') or {}
    d['privado'] = d.get('privado') or {}
    d['estado_label'] = ESTADOS.get(d['estado'], d['estado'])
    return d


def crear(q, user):
    ahora = now_iso()
    r = q('INSERT INTO carga_inmuebles (asesor_slug, estado, datos, privado, paso, created_at, updated_at) '
          'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id', (user['user'], 'borrador', '{}', '{}', 1, ahora, ahora), fetch=True)
    cid = r[0]['id']
    log_evento(q, cid, 'creada', user['user'])
    return cid


def una(q, cid):
    rows = q('SELECT * FROM carga_inmuebles WHERE id=%s', (cid,), fetch=True)
    return _row(rows[0]) if rows else None


def de_asesor(q, slug):
    return [_row(r) for r in q('SELECT * FROM carga_inmuebles WHERE asesor_slug=%s ORDER BY id DESC', (slug,), fetch=True) or []]


def cola(q, estados=('enviada',)):
    ph = ','.join(['%s'] * len(estados))
    return [_row(r) for r in q(f'SELECT * FROM carga_inmuebles WHERE estado IN ({ph}) ORDER BY id', tuple(estados), fetch=True) or []]


def todas(q):
    return [_row(r) for r in q('SELECT * FROM carga_inmuebles ORDER BY id DESC', fetch=True) or []]


def editable(c, user):
    return bool(c) and c['asesor_slug'] == user['user'] and c['estado'] in ('borrador', 'devuelta')


# ------------------------------------------------------------------ validación por paso
def _int(v, lo=0, hi=10**9):
    s = re.sub(r'[^\d]', '', str(v or ''))
    if s == '':
        return None
    n = int(s)
    return n if lo <= n <= hi else None


def _num(v, hi=10**7):
    s = str(v or '').strip().replace(',', '.')
    try:
        n = float(s)
    except ValueError:
        return None
    return n if 0 < n <= hi else None


def _txt(v, n):
    return re.sub(r'[ \t]+', ' ', str(v or '').replace('\r\n', '\n')).strip()[:n]


def limpiar_paso(paso, form):
    """Devuelve (datos_publicos, datos_privados) del paso, ya normalizados (lo inválido queda vacío)."""
    f = form
    pub, priv = {}, {}
    if paso == 1:
        pub['operacion'] = f.get('operacion') if f.get('operacion') in OPERACIONES else ''
        pub['tipo'] = f.get('tipo') if f.get('tipo') in TIPOS else ''
        pub['titulo'] = _txt(f.get('titulo'), 70)
        pub['precio'] = _int(f.get('precio'), 1, 99_999_999)
        pub['condominio'] = _int(f.get('condominio'), 0, 9_999_999)
        pub['estado_fisico'] = f.get('estado_fisico') if f.get('estado_fisico') in ESTADO_FISICO else ''
        pub['origen'] = f.get('origen') if f.get('origen') in ('propia', 'alianza') else ''
        pub['aliado'] = _txt(f.get('aliado'), 80) if pub['origen'] == 'alianza' else ''
    elif paso == 2:
        pub['municipio'] = f.get('municipio') if f.get('municipio') in MUNICIPIOS else ''
        pub['zona'] = _txt(f.get('zona'), 80)
        priv['direccion'] = _txt(f.get('direccion'), 200)
        lat, lng = _txt(f.get('lat'), 20), _txt(f.get('lng'), 20)
        try:
            ok = -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180
        except ValueError:
            ok = False
        priv['lat'], priv['lng'] = (lat, lng) if ok else ('', '')
    elif paso == 3:
        pub['habitaciones'] = _int(f.get('habitaciones'), 0, 29)
        pub['banos'] = _int(f.get('banos'), 0, 9)
        pub['estacionamientos'] = _int(f.get('estacionamientos'), 0, 29)
        pub['area_construida'] = _num(f.get('area_construida'))
        pub['area_privada'] = _num(f.get('area_privada'))
        pub['area_terreno'] = _num(f.get('area_terreno'))
        pub['piso'] = _int(f.get('piso'), 0, 60)
        pub['anio'] = _int(f.get('anio'), 1900, datetime.now().year + 3)
        pub['mascotas'] = f.get('mascotas') if f.get('mascotas') in ('si', 'no') else ''
        pub['depositos'] = _int(f.get('depositos'), 0, 20)
        pub['descripcion'] = re.sub(r'<[^>]+>', '', _txt(f.get('descripcion'), 5000))
    elif paso == 4:
        v = _txt(f.get('video'), 300)
        pub['video'] = v if re.match(r'^https://(www\.)?(youtube\.com/watch\?v=|youtu\.be/|vimeo\.com/)[\w\-?=&/]+$', v) else ''
        priv['propietario_nombre'] = _txt(f.get('propietario_nombre'), 120)
        priv['propietario_tel'] = re.sub(r'[^\d+]', '', str(f.get('propietario_tel') or ''))[:20]
        priv['nota_interna'] = _txt(f.get('nota_interna'), 1000)
    return pub, priv


def guardar_paso(q, c, paso, form, user):
    pub, priv = limpiar_paso(paso, form)
    datos, privado = dict(c['datos']), dict(c['privado'])
    datos.update(pub)
    privado.update(priv)
    q('UPDATE carga_inmuebles SET datos=%s, privado=%s, paso=%s, updated_at=%s WHERE id=%s',
      (json.dumps(datos, ensure_ascii=False), json.dumps(privado, ensure_ascii=False), max(c.get('paso') or 1, paso),
       now_iso(), c['id']))
    return datos, privado


def faltantes(c, n_fotos):
    """Lista de lo que falta para poder enviar (reglas del superconjunto Wasi/ML/Agentiz)."""
    d, falta = c['datos'], []
    req = [('operacion', 'Tipo de negocio'), ('tipo', 'Tipo de inmueble'), ('titulo', 'Título'), ('precio', 'Precio'),
           ('estado_fisico', 'Estado físico'), ('origen', 'Propia o alianza'), ('municipio', 'Municipio'), ('zona', 'Zona'),
           ('area_construida', 'Área construida'), ('mascotas', 'Mascotas (sí/no)')]
    for k, lbl in req:
        if d.get(k) in (None, ''):
            falta.append(lbl)
    if d.get('origen') == 'alianza' and not d.get('aliado'):
        falta.append('Nombre del aliado')
    if TIPOS.get(d.get('tipo') or '', (0, '', False))[2]:
        for k, lbl in (('habitaciones', 'Habitaciones'), ('banos', 'Baños')):
            if d.get(k) is None:
                falta.append(lbl)
    if d.get('estacionamientos') is None:
        falta.append('Estacionamientos (0 si no tiene)')
    if len(d.get('descripcion') or '') < 30:
        falta.append('Descripción (mínimo 30 caracteres)')
    if len(d.get('titulo') or '') < 10:
        falta.append('Título (mínimo 10 caracteres)') if 'Título' not in falta else None
    if n_fotos < FOTOS_MIN:
        falta.append(f'Fotos (mínimo {FOTOS_MIN}, tienes {n_fotos})')
    return falta


# ------------------------------------------------------------------ fotos
def foto_mime(data):
    for sig, m in MIME.items():
        if data.startswith(sig):
            return m
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def fotos(q, cid, con_datos=False):
    cols = '*' if con_datos else 'id, carga_id, pos, mime, tam, sha256, nombre, activa, wasi_url, created_at'
    return q(f'SELECT {cols} FROM carga_fotos WHERE carga_id=%s AND activa=1 ORDER BY pos, id', (cid,), fetch=True) or []


def agregar_foto(q, c, data, nombre, user):
    """(id, error). Valida tipo real por firma, tamaño, máximo de fotos y total por carga; ignora duplicadas exactas."""
    mime = foto_mime(data)
    if not mime:
        return None, 'Solo fotos JPG, PNG o WEBP.'
    if len(data) > FOTO_MAX_BYTES:
        return None, f'Cada foto puede pesar hasta {FOTO_MAX_BYTES // (1024 * 1024)} MB.'
    act = fotos(q, c['id'])
    if len(act) >= FOTOS_MAX:
        return None, f'Máximo {FOTOS_MAX} fotos.'
    if sum(int(f['tam']) for f in act) + len(data) > FOTOS_TOTAL_MAX:
        return None, 'Se alcanzó el peso total permitido para este inmueble.'
    h = hashlib.sha256(data).hexdigest()
    if any(f['sha256'] == h for f in act):
        return None, 'Esa foto ya estaba cargada.'
    pos = max([int(f['pos']) for f in act] or [0]) + 1
    r = q('INSERT INTO carga_fotos (carga_id, pos, mime, tam, sha256, nombre, datos, subido_por, created_at) '
          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
          (c['id'], pos, mime, len(data), h, (nombre or '')[:120], data, user['user'], now_iso()), fetch=True)
    return r[0]['id'], None


def quitar_foto(q, c, fid, user):
    q('UPDATE carga_fotos SET activa=0 WHERE id=%s AND carga_id=%s', (fid, c['id']))   # nunca se borra físicamente
    log_evento(q, c['id'], 'foto_quitada', user['user'], {'foto': fid})


def foto(q, fid):
    rows = q('SELECT id, carga_id, mime, datos FROM carga_fotos WHERE id=%s', (fid,), fetch=True)
    return rows[0] if rows else None


# ------------------------------------------------------------------ duplicados
def _palabras(s):
    return {w for w in re.findall(r'[a-z0-9]+', _norm(s)) if len(w) > 2}


def _precio_n(p):
    s = re.sub(r'\D', '', str(p or ''))
    return int(s) if s else None


def duplicados(c, inventario_items, otras_cargas):
    """Candidatos a duplicado: mismo negocio+tipo+zona y precio ±15 %, o título muy parecido (Jaccard ≥ 0,6)."""
    d = c['datos']
    out = []
    zona, op, tipo, precio = _norm(d.get('zona')), d.get('operacion'), d.get('tipo'), d.get('precio') or 0
    tw = _palabras(d.get('titulo'))

    def cerca(p):
        return p and precio and abs(p - precio) <= 0.15 * max(p, precio)

    for it in inventario_items:
        mismo = _norm(it.get('zona')) == zona and it.get('operacion') == op and _norm(it.get('tipo')) == _norm(tipo)
        p = _precio_n(it.get('precio'))
        ow = _palabras(it.get('titulo'))
        jac = len(tw & ow) / len(tw | ow) if tw and ow else 0
        if (mismo and cerca(p)) or jac >= 0.6:
            out.append({'fuente': f"Inventario ({it.get('origen') or ''})", 'titulo': it.get('titulo') or '',
                        'codigo': it.get('codigo') or '', 'precio': it.get('precio') or '', 'zona': it.get('zona') or '',
                        'motivo': 'mismo tipo, zona y precio parecido' if mismo and cerca(p) else 'título muy parecido'})
    for o in otras_cargas:
        if o['id'] == c['id'] or o['estado'] == 'rechazada':
            continue
        od = o['datos']
        mismo = _norm(od.get('zona')) == zona and od.get('operacion') == op and od.get('tipo') == tipo
        ow = _palabras(od.get('titulo'))
        jac = len(tw & ow) / len(tw | ow) if tw and ow else 0
        if (mismo and cerca(od.get('precio'))) or jac >= 0.6:
            out.append({'fuente': f"Carga #{o['id']} ({o['estado_label']})", 'titulo': od.get('titulo') or '', 'codigo': '',
                        'precio': f"US$ {od.get('precio')}" if od.get('precio') else '', 'zona': od.get('zona') or '',
                        'motivo': 'mismo tipo, zona y precio parecido' if mismo else 'título muy parecido'})
    return out


# ------------------------------------------------------------------ Wasi
def payload_wasi(c, n_fotos, portales=None):
    """Payload EXACTO de api.wasi.co/v1/property/add (sin credenciales). Dirección/propietario solo en «comment» (interno)."""
    d, p = c['datos'], c['privado']
    cat = catalogo()
    avisos = []
    tipo = TIPOS.get(d.get('tipo') or '')
    body = {
        'title': d.get('titulo') or '',
        'id_property_type': tipo[0] if tipo else None,
        'for_sale': 'true' if d.get('operacion') == 'Venta' else 'false',
        'for_rent': 'true' if d.get('operacion') == 'Alquiler' else 'false',
        'for_transfer': 'false',
        'id_currency': cat.get('id_currency_usd', 3),
        'sale_price': d.get('precio') if d.get('operacion') == 'Venta' else 0,
        'rent_price': d.get('precio') if d.get('operacion') == 'Alquiler' else 0,
        'maintenance_fee': d.get('condominio') or 0,
        'bedrooms': d.get('habitaciones') or 0, 'bathrooms': d.get('banos') or 0, 'garages': d.get('estacionamientos') or 0,
        'built_area': d.get('area_construida') or '', 'id_unit_built_area': 1,
        'private_area': d.get('area_privada') or '', 'id_unit_private_area': 1,
        'area': d.get('area_terreno') or '', 'id_unit_area': 1,
        'floor': d.get('piso') or '', 'building_date': d.get('anio') or '',
        'id_property_condition': ESTADO_FISICO.get(d.get('estado_fisico') or '', 2),
        'observations': d.get('descripcion') or '',
        'video': d.get('video') or '',
        'id_status_on_page': 1, 'id_availability': 1,
        'id_publish_on_map': 2 if p.get('lat') else 1,     # nunca «punto exacto»
        'latitude': p.get('lat') or '', 'longitude': p.get('lng') or '',
        'reference': f"PORTAL-{c['id']}",
        'network_share': 'false',
        'portals': list(portales if portales is not None else PORTALES_WASI_DEFECTO),
    }
    z = cat.get('zonas', {}).get(d.get('zona') or '')
    if not z:
        zn = {_norm(k): v for k, v in cat.get('zonas', {}).items()}
        z = zn.get(_norm(d.get('zona')))
    base = cat.get('zonas', {}).get('Sabana Grande') or {}
    if z:
        body.update({k: z[k] for k in ('id_country', 'id_region', 'id_city', 'id_location', 'id_zone') if z.get(k)})
    else:
        body.update({'id_country': base.get('id_country', 95), 'id_region': base.get('id_region', 3556),
                     'id_city': base.get('id_city', 859026), 'zone': d.get('zona') or ''})
        avisos.append(f"La zona «{d.get('zona')}» no existe en el catálogo Wasi: Wasi la crearía como zona nueva. Revisar.")
    uid = usuarios_wasi().get(c['asesor_slug'])
    if uid:
        body['id_user'] = uid
    else:
        avisos.append('La asesora no tiene usuario Wasi mapeado: quedaría a nombre del usuario del token.')
    interno = [f"Carga portal #{c['id']} · captadora: {c['asesor_slug']}",
               f"Origen: {d.get('origen') or ''}" + (f" · aliado: {d.get('aliado')}" if d.get('aliado') else ''),
               f"Dirección: {p.get('direccion') or '—'}",
               f"Propietario: {p.get('propietario_nombre') or '—'} {p.get('propietario_tel') or ''}".strip(),
               f"Mascotas: {d.get('mascotas') or '—'} · Depósitos: {d.get('depositos') if d.get('depositos') is not None else '—'}"]
    if p.get('nota_interna'):
        interno.append('Nota: ' + p['nota_interna'])
    body['comment'] = '\n'.join(interno)          # «Comentarios internos» de Wasi: no se publica
    if 43 in body['portals']:
        avisos.append('Incluye Mercado Libre: consume 1 cupo del paquete Plata (tope 100).')
    pasos = [{'metodo': 'POST', 'uri': 'https://api.wasi.co/v1/property/add', 'body': body}]
    pasos += [{'metodo': 'POST', 'uri': 'https://api.wasi.co/v1/property/upload-image/{id_property}',
               'multipart': {'image': f'<foto {i + 1}>', 'position': i + 1}} for i in range(n_fotos)]
    pasos.append({'metodo': 'POST', 'uri': 'https://api.wasi.co/v1/portal/send-property/{id_property}'})
    return {'pasos': pasos, 'avisos': avisos}


def modo_wasi():
    return 'live' if (os.environ.get('WASI_WRITE_MODE') or '').strip().lower() == 'live' else 'dry-run'


def publicar_wasi(q, c, fotos_lista, portales, user, log=None):
    """Por defecto DRY-RUN: arma el payload exacto, lo registra y NO escribe. 'live' no está habilitado en esta rama."""
    plan = payload_wasi(c, len(fotos_lista), portales)
    plan['fotos'] = [{'pos': i + 1, 'mime': f['mime'], 'bytes': int(f['tam']), 'sha256': f['sha256'][:16]}
                     for i, f in enumerate(fotos_lista)]
    modo = modo_wasi()
    if modo == 'live':
        # Rama de PRUEBA: la escritura real queda bloqueada hasta que José diga «publica» y se active con su OK.
        res = {'ok': False, 'modo': 'live', 'error': 'Escritura real deshabilitada en esta versión de prueba.'}
    else:
        res = {'ok': True, 'modo': 'dry-run', 'nota': 'No se escribió en Wasi. Payload registrado.'}
    if log:
        log.info('WASI %s carga=%s payload=%s', modo, c['id'], json.dumps(plan['pasos'][0]['body'], ensure_ascii=False)[:4000])
    q('UPDATE carga_inmuebles SET wasi_modo=%s, wasi_payload=%s, wasi_resultado=%s, portales_wasi=%s, updated_at=%s WHERE id=%s',
      (modo, json.dumps(plan, ensure_ascii=False), json.dumps(res, ensure_ascii=False), json.dumps(list(portales)),
       now_iso(), c['id']))
    log_evento(q, c['id'], f'wasi_{modo}', user['user'], {'portales': list(portales), 'avisos': plan['avisos']})
    return plan, res


# ------------------------------------------------------------------ Agentiz XML y kits
def _x(v):
    return escape(str(v if v is not None else ''))


def agentiz_xml(cargas, contacto, foto_url=None, ahora=None):
    """Feed Agentiz VE (UTF-8, sin HTML). Solo propias aprobadas; sin dirección ni datos internos.
    foto_url(foto) devuelve URL pública (Wasi) o None; sin URL pública la foto no se incluye."""
    ahora = ahora or datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<root>', f'<date-generated>{_x(ahora)}</date-generated>',
           '<contacts>', f"<name_company>{_x(contacto.get('empresa'))}</name_company>",
           f"<phone_code>{_x(contacto.get('codigo'))}</phone_code>", f"<phone_number>{_x(contacto.get('telefono'))}</phone_number>",
           '</contacts>']
    for c in cargas:
        d = c['datos']
        if c['estado'] != 'aprobada' or d.get('origen') != 'propia':
            continue
        tipo = TIPOS.get(d.get('tipo') or '', (0, 'business_other_na', False))
        out += ['<ad>', f"<deal_type>{'sale' if d.get('operacion') == 'Venta' else 'rent'}</deal_type>",
                f'<property_type>{tipo[1]}</property_type>', f"<external_id>FR-{c['id']}</external_id>",
                '<geo>', '<area_1>Distrito Capital</area_1>' if d.get('municipio') == 'Libertador' else '<area_1>Miranda</area_1>',
                f"<area_2>{_x(d.get('municipio'))}</area_2>", '<locality>Caracas</locality>',
                f"<sublocality_2>{_x(d.get('zona'))}</sublocality_2>", '</geo>',
                '<date>', f"<creation>{_x(c['created_at'])}</creation>", f"<updated>{_x(c['updated_at'])}</updated>", '</date>',
                '<desc>', '<interior>']
        if d.get('habitaciones') is not None:
            out.append(f"<bedrooms>{min(int(d['habitaciones']), 25)}</bedrooms>")
        if d.get('banos') is not None:
            out.append(f"<bathrooms>{min(int(d['banos']), 5)}</bathrooms>")
        if d.get('area_construida'):
            out.append(f"<size_s_total_metric>{float(d['area_construida']):.2f}</size_s_total_metric>")
        if d.get('piso'):
            out.append(f"<floor>{min(int(d['piso']), 15)}</floor>")
        out += ['</interior>', '<annex>', f"<header><es>{_x((d.get('titulo') or '')[:100])}</es></header>",
                f"<text><es>{_x((d.get('descripcion') or '')[:5000])}</es></text>", '</annex>', '</desc>',
                '<deal>', '<currency>USD</currency>', '<price>']
        if d.get('operacion') == 'Venta':
            out.append(f"<sale><sum_total>{int(d.get('precio') or 0)}</sum_total></sale>")
        else:
            out.append(f"<rental><per_month>{int(d.get('precio') or 0)}</per_month></rental>")
        out += ['</price>', '</deal>', '<media>']
        for f in c.get('_fotos') or []:
            u = foto_url(f) if foto_url else None
            if u:
                out.append(f'<image>{_x(u)}</image>')
        out += ['</media>', '</ad>']
    out.append('</root>')
    return '\n'.join(out)


def kit(c, portal):
    """Texto listo para copiar en un portal manual. Nunca incluye dirección, propietario ni notas internas."""
    d = c['datos']
    largo = LARGO_TITULO.get(portal, 70)
    titulo = (d.get('titulo') or '')[:largo]
    filas = [('Tipo de negocio', d.get('operacion')), ('Tipo de inmueble', d.get('tipo')),
             ('Precio (US$)', d.get('precio')), ('Condominio (US$)', d.get('condominio')),
             ('Municipio', d.get('municipio')), ('Zona / urbanización', d.get('zona')), ('Ciudad', 'Caracas'),
             ('Habitaciones', d.get('habitaciones')), ('Baños', d.get('banos')), ('Estacionamientos', d.get('estacionamientos')),
             ('Área construida (m²)', d.get('area_construida')), ('Área privada (m²)', d.get('area_privada')),
             ('Área terreno (m²)', d.get('area_terreno')), ('Piso', d.get('piso')), ('Año', d.get('anio')),
             ('Estado físico', d.get('estado_fisico')), ('Acepta mascotas', {'si': 'Sí', 'no': 'No'}.get(d.get('mascotas') or '')),
             ('Video', d.get('video'))]
    campos = [(k, v) for k, v in filas if v not in (None, '')]
    return {'portal': portal, 'titulo': titulo, 'descripcion': d.get('descripcion') or '', 'campos': campos,
            'nota': 'No pegar dirección exacta ni datos del propietario. Usar ubicación por zona.'}


def texto_social(c):
    d = c['datos']
    linea = ' · '.join(x for x in (f"{d.get('tipo')} en {d.get('operacion')}" if d.get('tipo') else '',
                                    d.get('zona'), f"US$ {d.get('precio'):,}".replace(',', '.') if d.get('precio') else '') if x)
    det = ' · '.join(x for x in (f"{d['habitaciones']} hab." if d.get('habitaciones') else '',
                                  f"{d['banos']} baños" if d.get('banos') else '',
                                  f"{d['area_construida']:g} m²" if d.get('area_construida') else '') if x)
    return (f"🏠 {d.get('titulo') or ''}\n{linea}\n{det}\n\nEscríbenos por DM para más información y visitas.\n"
            f"#ForRent #Inmuebles #Caracas #{_norm(d.get('zona')).replace(' ', '')}").strip()
