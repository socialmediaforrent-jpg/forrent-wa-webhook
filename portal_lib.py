"""Datos y autenticación del portal For Rent (asesores y José). Sin secretos en este archivo."""
import hashlib, hmac, json, os, re, unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from corte_clientes import en_ejercicio, filtrar, CORTE_FECHA, CORTE_ISO, CORTE_LABEL, CORTE_NOTA

CARACAS = ZoneInfo('America/Caracas')
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')

ESTATUS_OPCIONES = [
    'Nuevo', 'No contactado', 'Contactado', 'Buscando', 'Esperando',
    'Visita agendada', 'Visita realizada',
    'Oferta realizada', 'Oferta aceptada', 'Reserva agendada', 'Reserva firmada',
    'Documento privado', 'Documento notariado', 'Documento registrado',
    'Inmueble entregado', 'En espera', 'Recuperado', 'Perdido', 'Sin estatus',
]

# Fases del pipeline (solo agrupan; no cambian los valores de estatus).
FASE_DEFS = [
    ('f1', 'Fase 1 · Asignación y contacto', ('Nuevas Asignaciones', 'Clientes sin estatus', 'Contactado')),
    ('f2', 'Fase 2 · Búsqueda / En proceso', ('Buscando', 'Esperando', 'En espera', 'En proceso')),
    ('f3', 'Fase 3 · Visitas', ('Visita agendada', 'Visita realizada')),
    ('f4', 'Fase 4 · Negociación', ('Oferta realizada', 'Oferta aceptada', 'Reserva agendada')),
    ('f5', 'Fase 5 · Entrega', ('Reserva firmada',)),
]
_FASE_KEY = {k: fid for fid, _t, keys in FASE_DEFS for k in keys}


def _texto_perdido(s):
    """Reconoce estatus reales de pérdida/descarte (sin inventar valores)."""
    n = norm(s)
    if not n:
        return False
    if n in {
        'perdido', 'descartado', 'no interesado', 'cerrado', 'cerrado sin negocio',
        'cerrado sin exito', 'busca otra opcion', 'buscando otra propiedad',
    }:
        return True
    if n.startswith('busca otra propiedad') or n.startswith('buscando otra propiedad'):
        return True
    if 'desistio' in n or 'cerrado sin' in n:
        return True
    return False


def es_perdido(estado='', situacion='', estatus='', tg=''):
    if (estado or '').strip() == 'Perdido':
        return True
    return any(_texto_perdido(x) for x in (estado, situacion, estatus, tg))


def ahora_caracas():
    """Instante actual en America/Caracas. Se calcula al cargar la página, no se congela en la copia."""
    return datetime.now(CARACAS)


def parse_asignado_at(raw):
    """Lee el campo asignado_at. Si no trae zona, se interpreta en America/Caracas (igual que el corte)."""
    s = str(raw or '').strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        try:
            dt = datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CARACAS)
    return dt.astimezone(CARACAS)


def asignado_hace_menos_de_24h(asignado_at='', now=None):
    """True solo si hay fecha/hora de asignación y aún no se cumplen 24 horas."""
    dt = parse_asignado_at(asignado_at)
    if dt is None:
        return False
    ahora = now or ahora_caracas()
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=CARACAS)
    return (ahora.astimezone(CARACAS) - dt).total_seconds() < 24 * 3600


# ASIGNADO, No contactado, Nuevo, Sin estatus o vacío no cuentan como avance.
_SIN_AVANCE = frozenset({'', 'sin estatus', 'asignado', 'no contactado', 'nuevo'})


def es_sin_avance(estado='', situacion='', estatus='', tg=''):
    """Sin avance: no está perdido y el primer valor (situacion, estado, estatus) no es un estatus real."""
    if es_perdido(estado, situacion, estatus, tg):
        return False
    for raw in (situacion, estado, estatus):
        v = (raw or '').strip()
        if not v:
            continue
        return v.casefold() in _SIN_AVANCE
    return True


def destino_sin_avance(asignado_at='', now=None):
    """nuevas si lleva menos de 24 h; sin_estatus si lleva 24 h o más, o no tiene fecha."""
    if asignado_hace_menos_de_24h(asignado_at, now=now):
        return 'nuevas'
    return 'sin_estatus'


def fase_de(estado='', situacion='', estatus='', tg='', asignado_at='', now=None):
    """((id de fase, etiqueta de desglose)). Prioriza estado=Perdido sobre Buscando/Esperando."""
    e, si, st = (estado or '').strip(), (situacion or '').strip(), (estatus or '').strip()
    if es_perdido(e, si, st, tg):
        if e == 'Perdido' and si:
            return 'perdidos', f'Perdido · {si}'
        if e == 'Perdido':
            return 'perdidos', 'Perdido'
        return 'perdidos', si or st or (tg or '').strip() or e or 'Perdido'
    if es_sin_avance(e, si, st, tg):
        dest = destino_sin_avance(asignado_at, now=now)
        if dest == 'nuevas':
            return 'f1', 'Nuevas Asignaciones'
        return 'f1', 'Clientes sin estatus'
    if si in _FASE_KEY:
        return _FASE_KEY[si], si
    if e in _FASE_KEY:
        return _FASE_KEY[e], e
    if st in _FASE_KEY:
        return _FASE_KEY[st], st
    return 'otros', si or e or st or 'Sin clasificar'


def tiene_estatus(estado='', situacion='', estatus=''):
    e, st = (estado or '').strip(), (estatus or '').strip()
    return (e and e != 'Sin estatus') or (st and st != 'Sin estatus')


def resumen_fases(clientes, *, now=None):
    """Agrupa clientes por fase. Cada ítem puede traer estado, situacion, estatus y tg."""
    from collections import Counter
    ahora = now or ahora_caracas()
    orden = [fid for fid, _t, _k in FASE_DEFS] + ['perdidos', 'otros']
    titulos = {fid: t for fid, t, _k in FASE_DEFS}
    titulos['perdidos'] = 'Perdidos'
    titulos['otros'] = 'Otros'
    buckets = {fid: {'id': fid, 'titulo': titulos[fid], 'desglose': Counter(), 'clientes': []} for fid in orden}
    con = 0
    for c in clientes:
        c = dict(c)
        e, si, st = c.get('estado') or '', c.get('situacion') or '', c.get('estatus') or c.get('status') or ''
        fid, lab = fase_de(e, si, st, c.get('tg') or '', c.get('asignado_at') or '', now=ahora)
        c['fase'], c['fase_label'] = fid, lab
        buckets[fid]['clientes'].append(c)
        buckets[fid]['desglose'][lab] += 1
        if tiene_estatus(e, si, st):
            con += 1
    grupos = []
    for fid in orden:
        b = buckets[fid]
        if fid == 'otros' and not b['clientes']:
            continue
        keys = next((k for i, _t, k in FASE_DEFS if i == fid), ())
        seen = set()
        des = []
        for k in keys:
            # desglose may use the key directly
            n = b['desglose'].get(k, 0)
            if n:
                des.append((k, n)); seen.add(k)
        for k, n in b['desglose'].most_common():
            if k not in seen:
                des.append((k, n))
        grupos.append({'id': fid, 'titulo': b['titulo'], 'total': len(b['clientes']), 'desglose': des, 'clientes': b['clientes']})
    total = len(clientes)
    pct = int(round(100 * con / total)) if total else 0
    return {'total': total, 'con': con, 'pct': pct, 'grupos': grupos}


# Embudo «Mis Asignaciones» (vista nueva; no altera FASE_DEFS ni los valores de estatus).
# Cada baldosa usa solo estatus ya existentes. Si no hay clientes en ese estatus, el conteo es 0.
# No se inventan números ni se crean estatus nuevos.
#
# Mapeo (prioridad: situacion, luego estado, luego estatus; es_perdido va primero
# para no duplicar Perdido·Esperando / Perdido·Buscando en otra baldosa):
#   Nuevas Asignaciones           → sin avance y asignados hace menos de 24 horas
#                                   (ASIGNADO, No contactado, Nuevo, Sin estatus o vacío)
#   Clientes sin estatus          → sin avance y con 24 horas o más desde asignado_at
#                                   (o sin fecha de asignación)
#   Contactado                    → Contactado
#   En Búsqueda/Esperando         → Buscando, Esperando, En espera
#   Agendado/Visitado             → Visita agendada, Visita realizada
#   Oferta enviada/recibida       → Oferta realizada
#   Oferta aceptada               → Oferta aceptada
#   Reserva agendada              → Reserva agendada
#   Reserva Firmada               → Reserva firmada
#   Inmueble Registrado/Notariado → Documento notariado, Documento registrado
#   Inmueble Entregado            → Inmueble entregado
#   Otros                         → cualquier otro estatus no mapeado (nunca se pierde)
#   Perdidos                      → estado=Perdido o textos ya reconocidos por es_perdido
ASIGNACIONES_ESTATUS = {
    'Nuevo': 'nuevas',
    'No contactado': 'nuevas',
    'ASIGNADO': 'nuevas',
    'Asignado': 'nuevas',
    'Sin estatus': 'sin_estatus',
    'Contactado': 'contactado',
    'Buscando': 'busqueda',
    'Esperando': 'busqueda',
    'En espera': 'busqueda',
    'Visita agendada': 'agendado',
    'Visita realizada': 'agendado',
    'Oferta realizada': 'oferta',
    'Oferta aceptada': 'oferta_aceptada',
    'Reserva agendada': 'reserva_agendada',
    'Reserva firmada': 'reserva_firmada',
    'Documento notariado': 'registrado',
    'Documento registrado': 'registrado',
    'Inmueble entregado': 'entregado',
    'Otros': 'otros',
    'Perdido': 'perdidos',
}
ASIGNACIONES_RECONOCIDOS = set(ESTATUS_OPCIONES) | {
    'En proceso', 'Negociación', 'En espera', 'Esperando',
}
ASIGNACIONES_EMBUDO = (
    {
        'id': 'nuevas', 'n': 1, 'titulo': 'Nuevas Asignaciones', 'tono': 'peach',
        'icono': 'userplus', 'simple': True,
        'tiles': (
            {'id': 'nuevas', 'label': 'Nuevas Asignaciones', 'icono': 'userplus',
             'estatus': ('Nuevo', 'No contactado', 'ASIGNADO', 'Asignado')},
        ),
    },
    {
        'id': 'sin_estatus', 'n': None, 'titulo': 'Clientes sin estatus', 'tono': 'sand',
        'icono': 'clipboard', 'simple': True,
        'tiles': (
            {'id': 'sin_estatus', 'label': 'Clientes sin estatus', 'icono': 'clipboard',
             'estatus': ('Sin estatus',)},
        ),
    },
    {
        'id': 'proceso', 'n': 2, 'titulo': 'En Proceso', 'tono': 'gray',
        'icono': 'refresh', 'simple': False,
        'tiles': (
            {'id': 'contactado', 'label': 'Contactado', 'icono': 'phone',
             'estatus': ('Contactado',)},
            {'id': 'busqueda', 'label': 'En Búsqueda/Esperando', 'icono': 'search',
             'estatus': ('Buscando', 'Esperando', 'En espera')},
            {'id': 'agendado', 'label': 'Agendado/Visitado', 'icono': 'calendar-check',
             'estatus': ('Visita agendada', 'Visita realizada')},
        ),
    },
    {
        'id': 'negociacion', 'n': 3, 'titulo': 'En Negociación', 'tono': 'lavender',
        'icono': 'handshake', 'simple': False,
        'tiles': (
            {'id': 'oferta', 'label': 'Oferta enviada/recibida', 'icono': 'send',
             'estatus': ('Oferta realizada',)},
            {'id': 'oferta_aceptada', 'label': 'Oferta aceptada', 'icono': 'check-circle',
             'estatus': ('Oferta aceptada',)},
            {'id': 'reserva_agendada', 'label': 'Reserva agendada', 'icono': 'calendar-clock',
             'estatus': ('Reserva agendada',)},
        ),
    },
    {
        'id': 'entrega', 'n': 4, 'titulo': 'Entrega de Inmueble', 'tono': 'green',
        'icono': 'house', 'simple': False,
        'tiles': (
            {'id': 'reserva_firmada', 'label': 'Reserva Firmada', 'icono': 'doc-check',
             'estatus': ('Reserva firmada',)},
            {'id': 'registrado', 'label': 'Inmueble Registrado/Notariado', 'icono': 'stamp',
             'estatus': ('Documento notariado', 'Documento registrado')},
            {'id': 'entregado', 'label': 'Inmueble Entregado', 'icono': 'key',
             'estatus': ('Inmueble entregado',)},
        ),
    },
    {
        'id': 'otros', 'n': None, 'titulo': 'Otros', 'tono': 'stone',
        'icono': 'tag', 'simple': True,
        'tiles': (
            {'id': 'otros', 'label': 'Otros', 'icono': 'tag',
             'estatus': ('Otros',)},
        ),
    },
    {
        'id': 'perdidos', 'n': None, 'titulo': 'Perdidos', 'tono': 'rose',
        'icono': 'x-circle', 'simple': True,
        'tiles': (
            {'id': 'perdidos', 'label': 'Perdidos', 'icono': 'x-circle',
             'estatus': ('Perdido',)},
        ),
    },
)
_TILE_DEFS = {t['id']: {**t, 'fase_id': f['id'], 'fase_titulo': f['titulo']}
              for f in ASIGNACIONES_EMBUDO for t in f['tiles']}


_ESTATUS_CF = {k.casefold(): k for k in ASIGNACIONES_ESTATUS}


def _clave_estatus(v):
    """Devuelve la clave canónica (respeta mayúsculas del mapa) o None."""
    return _ESTATUS_CF.get((v or '').strip().casefold())


def canon_estatus_asignacion(estado='', situacion='', estatus='', tg=''):
    """Valor de estatus que alimenta el embudo. Nunca deja un cliente sin baldosa."""
    if es_perdido(estado, situacion, estatus, tg):
        return 'Perdido'
    encontrados = [(raw or '').strip() for raw in (situacion, estado, estatus)]
    if not any(encontrados):
        return 'Sin estatus'
    for v in encontrados:
        if not v:
            continue
        key = _clave_estatus(v)
        if key and key not in ('Otros',):
            return key
    return 'Otros'


def tile_de_asignacion(estado='', situacion='', estatus='', tg='', asignado_at='', now=None):
    if es_perdido(estado, situacion, estatus, tg):
        return 'perdidos'
    if es_sin_avance(estado, situacion, estatus, tg):
        return destino_sin_avance(asignado_at, now=now)
    v = canon_estatus_asignacion(estado, situacion, estatus, tg)
    return ASIGNACIONES_ESTATUS.get(v) or 'otros'


def resumen_asignaciones(clientes, *, now=None):
    """Conteos reales por baldosa. No inventa filas ni cifras. La regla de 24 h se calcula al cargar."""
    ahora = now or ahora_caracas()
    buckets = {tid: [] for tid in _TILE_DEFS}
    for c in clientes:
        c = dict(c)
        e, si, st = c.get('estado') or '', c.get('situacion') or '', c.get('estatus') or c.get('status') or ''
        tid = tile_de_asignacion(e, si, st, c.get('tg') or '', c.get('asignado_at') or '', now=ahora)
        fid, lab = fase_de(e, si, st, c.get('tg') or '', c.get('asignado_at') or '', now=ahora)
        c['fase'], c['fase_label'] = fid, lab
        c['tile'] = tid
        if tid:
            buckets[tid].append(c)
    fases = []
    for f in ASIGNACIONES_EMBUDO:
        tiles = []
        for t in f['tiles']:
            lista = buckets[t['id']]
            tiles.append({**t, 'n': len(lista), 'clientes': lista})
        fases.append({**f, 'tiles': tiles, 'n_total': sum(t['n'] for t in tiles)})
    return {
        'fases': fases,
        'tiles': {t['id']: t for f in fases for t in f['tiles']},
        'en_embudo': sum(f['n_total'] for f in fases),
        'total': len(clientes),
    }


# Campos de comisión que un asesor SÍ puede ver. Nada de líder ni totales que permitan inferirlo.
ASESOR_CALC_KEYS = ('asesor', 'forrent', 'inmobiliaria')
LIDER_KEY_RE = re.compile(r'lider|líder|leader', re.I)


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '').lower() if unicodedata.category(c) != 'Mn').strip()


# Datos vivos: app.py registra un lector (tabla portal_datos, sincronizada desde la caja).
# Si no hay dato vivo, se usa la copia empaquetada en data/.
_LIVE_LOADER = None


def set_live_loader(fn):
    global _LIVE_LOADER
    _LIVE_LOADER = fn


def _load(name, default):
    if _LIVE_LOADER is not None:
        try:
            d = _LIVE_LOADER(name)
            if d is not None:
                return d
        except Exception:
            pass
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def asesores_meta():
    return _load('asesores.json', {}).get('asesores') or []


def asesor_by_user(user):
    user = (user or '').strip().lower()
    for a in asesores_meta():
        if a.get('user') == user:
            return a
    return None


def _hashes():
    raw = os.environ.get('PORTAL_ASESORES_SHA256', '')
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k).strip().lower(): str(v).strip().lower() for k, v in data.items() if v}
    except Exception:
        return {}


def sha256_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


# Slug interno del administrador (sesiones, permisos, notificaciones, datos).
# El usuario de entrada es PANEL_USERNAME (por defecto pintojosedaniel).
ADMIN_SLUG = 'jose'
DEFAULT_PANEL_USERNAME = 'pintojosedaniel'


def panel_username():
    """Usuario con el que entra el administrador. Vacío o ausente: el predeterminado."""
    raw = (os.environ.get('PANEL_USERNAME') or DEFAULT_PANEL_USERNAME).strip().lower()
    return raw or DEFAULT_PANEL_USERNAME


def panel_perfil():
    return {'user': ADMIN_SLUG, 'name': 'José Daniel Pinto Chacón', 'role': 'admin', 'equipo': [], 'lider': ''}


def es_login_admin(username):
    return (username or '').strip().lower() == panel_username()


MERCADEO_USER = 'mercadeo'
MERCADEO_PERFIL = {'user': MERCADEO_USER, 'name': 'Mercadeo For Rent', 'role': 'mercadeo', 'equipo': [], 'lider': ''}


INVENTARIO_USER = 'inventario'
INVENTARIO_PERFIL = {'user': INVENTARIO_USER, 'name': 'Inventario For Rent', 'role': 'inventario', 'equipo': [], 'lider': ''}


def _inventario_activo():
    return bool(os.environ.get('INVENTARIO_PASSWORD') or os.environ.get('INVENTARIO_PASSWORD_SHA256'))


def _inventario_password_ok(password):
    """Login propio de Inventario (solo lectura de Inmuebles): INVENTARIO_PASSWORD_SHA256 (o INVENTARIO_PASSWORD)."""
    plain, sha = os.environ.get('INVENTARIO_PASSWORD'), os.environ.get('INVENTARIO_PASSWORD_SHA256')
    if plain:
        return hmac.compare_digest(password, plain)
    if sha:
        return hmac.compare_digest(sha256_hex(password), sha.strip().lower())
    return False


def _mercadeo_password_ok(password):
    """Login propio de Mercadeo: MERCADEO_PASSWORD_SHA256 (o MERCADEO_PASSWORD). Sin variable, no entra nadie."""
    plain, sha = os.environ.get('MERCADEO_PASSWORD'), os.environ.get('MERCADEO_PASSWORD_SHA256')
    if plain:
        return hmac.compare_digest(password, plain)
    if sha:
        return hmac.compare_digest(sha256_hex(password), sha.lower())
    return False


def identify_user(username, password):
    """Devuelve el usuario autenticado o None. No registra la contraseña."""
    if not username or not password:
        return None
    user = username.strip().lower()
    if user == MERCADEO_USER:
        return dict(MERCADEO_PERFIL) if _mercadeo_password_ok(password) else None
    if user == INVENTARIO_USER:
        return dict(INVENTARIO_PERFIL) if _inventario_password_ok(password) else None
    if es_login_admin(user):
        plain, sha = os.environ.get('PANEL_PASSWORD'), os.environ.get('PANEL_PASSWORD_SHA256')
        ok = False
        if plain:
            ok = hmac.compare_digest(password, plain)
        elif sha:
            ok = hmac.compare_digest(sha256_hex(password), sha.strip().lower())
        if ok:
            return panel_perfil()
        return None
    expected = _hashes().get(user)
    if not expected:
        return None
    if not hmac.compare_digest(sha256_hex(password), expected):
        return None
    meta = asesor_by_user(user)
    if not meta:
        return None
    return {
        'user': user,
        'name': meta.get('name') or user,
        'role': meta.get('role') or 'asesor',
        'equipo': list(meta.get('equipo') or []),
        'lider': meta.get('lider') or '',
    }


def user_by_username(username):
    """Carga el perfil de un usuario ya autenticado (sesión). Sin contraseña."""
    user = (username or '').strip().lower()
    if not user:
        return None
    if user == ADMIN_SLUG:
        return panel_perfil()
    if user == MERCADEO_USER:
        # Si se quita la contraseña de Mercadeo del entorno, la sesión abierta también deja de valer.
        if os.environ.get('MERCADEO_PASSWORD') or os.environ.get('MERCADEO_PASSWORD_SHA256'):
            return dict(MERCADEO_PERFIL)
        return None
    if user == INVENTARIO_USER:
        # Sin variable de contraseña, la sesión abierta también deja de valer.
        return dict(INVENTARIO_PERFIL) if _inventario_activo() else None
    meta = asesor_by_user(user)
    if not meta:
        return None
    return {
        'user': user,
        'name': meta.get('name') or user,
        'role': meta.get('role') or 'asesor',
        'equipo': list(meta.get('equipo') or []),
        'lider': meta.get('lider') or '',
    }


def visible_slugs(user):
    """Slugs de asesor cuyos datos puede ver este usuario."""
    if user['role'] in ('mercadeo', 'inventario'):
        return []
    if user['role'] == 'admin':
        return [a['user'] for a in asesores_meta()]
    if user['role'] == 'lider':
        return [user['user']] + list(user.get('equipo') or [])
    return [user['user']]


def can_edit_propio(user, asesor_slug):
    if user['role'] == 'admin':
        return True
    return user['user'] == asesor_slug


def load_captaciones(slug=None, slugs=None):
    items = _load('captaciones.json', {}).get('items') or []
    allow = {slug} if slug else set(slugs or [])
    if allow:
        items = [x for x in items if x.get('asesor') in allow]
    return items


def load_asignados(slug=None, slugs=None, *, todos=False):
    data = _load('clientes_asignados.json', {})
    items = data.get('clientes') or []
    allow = {slug} if slug else set(slugs or [])
    if allow:
        items = [x for x in items if x.get('asesor') in allow]
    # Corte 31-oct-2026: no borrar; solo ocultar del ejercicio vigente.
    if not todos:
        items = [x for x in items if en_ejercicio(asignado_at=x.get('asignado_at'))]
    return items, data.get('actualizado') or '', data.get('fuente') or ''


def _pick_amount(calc, *keys):
    if not isinstance(calc, dict):
        return None
    for k in keys:
        if k in calc and calc[k] is not None and calc[k] != '':
            return calc[k]
    return None


def cierre_publico(cierre, *, full):
    """Copia de un cierre. Si full=False, solo comisión del asesor, parte For Rent e inmobiliaria aliada."""
    out = {
        'id': cierre.get('id') or '',
        'fecha': cierre.get('fecha') or '',
        'operacion': cierre.get('operacion') or '',
        'inmueble': cierre.get('inmueble') or '',
        'codigo': cierre.get('codigo') or '',
        'origen': cierre.get('origen') or '',
        'estado_pago': cierre.get('estado_pago') or '',
        'cliente': cierre.get('cliente') or '',
        'prueba': bool(cierre.get('prueba')),
        'asesor': cierre.get('asesor'),
        'fuente': cierre.get('fuente') or '',
    }
    calc = cierre.get('calc') or {}
    if not isinstance(calc, dict):
        calc = {}
    if full:
        # Líderes/José ven el cálculo tal como está en la fuente, incluida la parte del líder si existiera.
        out['calc'] = dict(calc)
        return out
    asesor = _pick_amount(calc, 'asesor', 'asesor_usd', 'monto_asesor')
    # Parte For Rent = lado For Rent si viene; si también hay resto de inmobiliaria
    # y el lado incluye al asesor, usamos el resto como parte de la empresa.
    forrent_lado = _pick_amount(calc, 'forrent', 'punta_forrent_usd')
    empresa = _pick_amount(calc, 'inmobiliaria', 'inmobiliaria_usd', 'monto_inmobiliaria')
    origen = (cierre.get('origen') or '').lower()
    aliada = None
    if 'alianza' in origen or 'aliado' in origen:
        aliada = empresa
        parte_fr = forrent_lado
        if parte_fr is not None and asesor is not None:
            try:
                if float(parte_fr) >= float(asesor) and empresa is not None:
                    parte_fr = empresa
            except (TypeError, ValueError):
                pass
    else:
        # En propios, «inmobiliaria» del cálculo es la parte de For Rent (la empresa), no un aliado.
        parte_fr = empresa if empresa is not None else forrent_lado
        if parte_fr is not None and asesor is not None and empresa is None:
            try:
                if forrent_lado is not None and abs(float(forrent_lado) - float(asesor)) < 0.01:
                    parte_fr = forrent_lado
            except (TypeError, ValueError):
                pass
    slim = {}
    if asesor is not None:
        slim['asesor'] = asesor
    if parte_fr is not None:
        slim['forrent'] = parte_fr
    if aliada is not None:
        slim['inmobiliaria'] = aliada
    # Defensa: nunca dejar pasar claves de líder.
    out['calc'] = {k: v for k, v in slim.items() if k in ASESOR_CALC_KEYS and not LIDER_KEY_RE.search(k)}
    return out


def load_cierres(slug=None, slugs=None, *, full):
    items = _load('cierres.json', {}).get('cierres') or []
    allow = {slug} if slug else set(slugs or [])
    out = []
    for c in items:
        a = c.get('asesor')
        if not a:
            if full:
                out.append(cierre_publico(c, full=True))
            continue
        if allow and a not in allow:
            continue
        out.append(cierre_publico(c, full=full))
    # Sin asignado_at en el libro de cierres: se usa la fecha del cierre.
    # Si tampoco hay fecha, se oculta.
    visibles = []
    for c in out:
        if en_ejercicio(asignado_at=c.get('asignado_at') or c.get('fecha')):
            visibles.append(c)
    return visibles


def money(x):
    if x is None or x == '':
        return '—'
    try:
        n = float(x)
    except (TypeError, ValueError):
        return str(x)
    s = f'{n:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return 'US$ ' + s


def parse_caracas(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CARACAS)
    except Exception:
        return None


MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
         'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')


def fmt_fecha(iso):
    dt = parse_caracas(iso) if iso and 'T' in str(iso) else None
    if dt:
        return f'{dt.day} de {MESES[dt.month - 1]} de {dt.year}'
    s = str(iso or '')[:10]
    try:
        dt = datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=CARACAS)
        return f'{dt.day} de {MESES[dt.month - 1]} de {dt.year}'
    except Exception:
        return s or '—'


def normalizar_telefono(s):
    s = (s or '').strip()
    if not s:
        return None, 'Indica el teléfono'
    if len(s) > 40:
        return None, 'El teléfono es demasiado largo'
    digits = ''.join(c for c in s if c.isdigit())
    if digits.startswith('00'):
        digits = digits[2:]
    # 0414... / 0424... (11 dígitos locales VE) -> 58 + sin el 0
    if digits.startswith('0') and len(digits) == 11:
        digits = '58' + digits[1:]
    if len(digits) < 10 or len(digits) > 15:
        return None, 'El teléfono debe tener entre 10 y 15 dígitos'
    return '+' + digits, None


def validar_cliente(form):
    errores = []
    nombre = re.sub(r'\s+', ' ', (form.get('nombre') or '').strip())
    if len(nombre) < 2 or len(nombre) > 120:
        errores.append('Indica el nombre del cliente (2 a 120 caracteres).')
    tel, err = normalizar_telefono(form.get('telefono'))
    if err:
        errores.append(err)
    operacion = (form.get('operacion') or '').strip()
    if operacion and operacion not in ('Venta', 'Alquiler'):
        errores.append('La operación debe ser Venta o Alquiler.')
    tipo = re.sub(r'\s+', ' ', (form.get('tipo') or '').strip())[:80]
    zona = re.sub(r'\s+', ' ', (form.get('zona') or '').strip())[:120]
    presupuesto = re.sub(r'\s+', ' ', (form.get('presupuesto') or '').strip())[:80]
    origen_nota = re.sub(r'\s+', ' ', (form.get('origen_nota') or '').strip())[:400]
    status = (form.get('status') or 'Nuevo').strip()
    if status not in ESTATUS_OPCIONES:
        errores.append('El estatus no es válido.')
    datos = dict(nombre=nombre, telefono=tel or '', operacion=operacion, tipo=tipo,
                 zona=zona, presupuesto=presupuesto, origen_nota=origen_nota, status=status)
    return datos, errores


def csrf_token(user):
    seed = (os.environ.get('PANEL_PASSWORD_SHA256') or os.environ.get('WA_READ_KEY_SHA256') or 'dev-portal')
    return hmac.new(seed.encode(), f"csrf:{user['user']}".encode(), hashlib.sha256).hexdigest()


def csrf_ok(user, given):
    if not given:
        return False
    return hmac.compare_digest(csrf_token(user), given)
