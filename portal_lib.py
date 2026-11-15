"""Datos y autenticación del portal For Rent (asesores y José). Sin secretos en este archivo."""
import hashlib, hmac, json, os, re, unicodedata
from datetime import datetime, timezone
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
    ('f1', 'Fase 1 · Asignación y contacto', ('Sin estatus', 'Nuevo', 'Contactado', 'No contactado')),
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


def fase_de(estado='', situacion='', estatus='', tg=''):
    """((id de fase, etiqueta de desglose)). Prioriza estado=Perdido sobre Buscando/Esperando."""
    e, si, st = (estado or '').strip(), (situacion or '').strip(), (estatus or '').strip()
    if es_perdido(e, si, st, tg):
        if e == 'Perdido' and si:
            return 'perdidos', f'Perdido · {si}'
        if e == 'Perdido':
            return 'perdidos', 'Perdido'
        return 'perdidos', si or st or (tg or '').strip() or e or 'Perdido'
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


def resumen_fases(clientes):
    """Agrupa clientes por fase. Cada ítem puede traer estado, situacion, estatus y tg."""
    from collections import Counter
    orden = [fid for fid, _t, _k in FASE_DEFS] + ['perdidos', 'otros']
    titulos = {fid: t for fid, t, _k in FASE_DEFS}
    titulos['perdidos'] = 'Perdidos'
    titulos['otros'] = 'Otros'
    buckets = {fid: {'id': fid, 'titulo': titulos[fid], 'desglose': Counter(), 'clientes': []} for fid in orden}
    con = 0
    for c in clientes:
        c = dict(c)
        e, si, st = c.get('estado') or '', c.get('situacion') or '', c.get('estatus') or c.get('status') or ''
        fid, lab = fase_de(e, si, st, c.get('tg') or '')
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


# Campos de comisión que un asesor SÍ puede ver. Nada de líder ni totales que permitan inferirlo.
ASESOR_CALC_KEYS = ('asesor', 'forrent', 'inmobiliaria')
LIDER_KEY_RE = re.compile(r'lider|líder|leader', re.I)


def norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '').lower() if unicodedata.category(c) != 'Mn').strip()


def _load(name, default):
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


def identify_user(username, password):
    """Devuelve el usuario autenticado o None. No registra la contraseña."""
    if not username or not password:
        return None
    user = username.strip().lower()
    if user == 'jose':
        plain, sha = os.environ.get('PANEL_PASSWORD'), os.environ.get('PANEL_PASSWORD_SHA256')
        ok = False
        if plain:
            ok = hmac.compare_digest(password, plain)
        elif sha:
            ok = hmac.compare_digest(sha256_hex(password), sha.lower())
        if ok:
            return {'user': 'jose', 'name': 'José Daniel Pinto Chacón', 'role': 'admin', 'equipo': [], 'lider': ''}
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
    if user == 'jose':
        return {'user': 'jose', 'name': 'José Daniel Pinto Chacón', 'role': 'admin', 'equipo': [], 'lider': ''}
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
