"""For Rent - receptor de webhooks de WhatsApp Cloud API (Meta).

Endpoints
  GET  /health                       -> estado (sin secretos)
  GET  /webhook                      -> handshake de verificación de Meta (hub.mode / hub.verify_token / hub.challenge)
  POST /webhook                      -> eventos de Meta (mensajes y estados), valida X-Hub-Signature-256
  GET  /api/events?since_id=N        -> (Bearer WA_READ_KEY) mensajes/estados nuevos para los scripts de la caja
  POST /api/outbound                 -> (Bearer WA_READ_KEY) registra una respuesta enviada desde la caja
  GET  /api/last_inbound?wa_id=...   -> (Bearer WA_READ_KEY) último mensaje entrante de un número
  GET  /privacidad                   -> política de privacidad (pública, sin login)
  GET  /eliminar-datos               -> cómo solicitar la eliminación de datos (pública, sin login)
  GET  /terminos                     -> términos de servicio breves (pública, sin login)
  GET  /login                        -> inicio de sesión (cookie de sesión firmada)
  POST /login, GET|POST /logout      -> entrar / cerrar sesión
  GET  / y /portal                   -> portal For Rent (sesión; redirige a /login)
  GET  /panel                        -> bandeja de entrada (sesión; José ve todas, asesores solo las suyas, solo lectura)
  GET  /panel/data                   -> JSON de la bandeja (para auto-actualizar)
  POST /panel/responder              -> responder por WhatsApp desde la bandeja (sesión + CSRF; ventana 24 h; solo
                                        si META_WA_TOKEN y WA_PHONE_NUMBER_ID existen). Mismos permisos que /panel.
  GET  /inventario                   -> inventario propios / alianza / red (solo lectura)

Variables de entorno
  DATABASE_URL                Postgres (Render). Si falta, usa SQLite local (solo pruebas).
  WA_VERIFY_TOKEN             token de verificación del webhook (o WA_VERIFY_TOKEN_SHA256 = sha256 hex del token)
  WA_READ_KEY                 llave Bearer de lectura (o WA_READ_KEY_SHA256 = sha256 hex de la llave)
  META_APP_SECRET             secreto de la app de Meta. Sin él NO se valida la firma (se registra un aviso).
  AUTO_REPLY_ENABLED=1        respuesta automática fuera de horario (apagada por defecto)
  AUTO_REPLY_TEXT, META_WA_TOKEN, WA_PHONE_NUMBER_ID, GRAPH_VERSION  (solo si AUTO_REPLY_ENABLED=1)
  PANEL_PASSWORD_SHA256       sha256 hex de la contraseña del portal (usuario fijo: jose). También acepta PANEL_PASSWORD.
  PORTAL_ESTATUS_URL          enlace del panel admin del Sistema de Clientes
  PORTAL_CIERRES_URL          enlace del panel admin de Cierres (Sistema Contable)
  SECRET_KEY                  firma de la cookie de sesión del portal (no es una contraseña de usuario)
  META_WA_TOKEN, WA_PHONE_NUMBER_ID  también activan el botón «Responder» de la bandeja (sin ellas queda deshabilitado)
  META_SOCIAL_INBOX=1         muestra Instagram/Facebook en la bandeja (solo admin, solo lectura) usando
                              META_PAGE_TOKEN o META_PAGE_TOKEN_FILE (token de página). Apagado por defecto.
"""
import csv, hashlib, hmac, json, logging, os, sqlite3, threading, time, urllib.request
from pathlib import Path as _Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from functools import wraps
from urllib.parse import quote

from flask import Flask, Response, abort, g, jsonify, make_response, redirect, render_template, request, session

import notif
import portal_lib
import mensajes
import portales
import bandeja
import redes
import marketplace

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('wa')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config.update(
    SESSION_COOKIE_NAME='forrent_session',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER')),
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
CARACAS = ZoneInfo('America/Caracas')
STARTED = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------- base de datos
DB_URL = os.environ.get('DATABASE_URL', '')
IS_PG = DB_URL.startswith('postgres')
_lock = threading.Lock()

if IS_PG:
    import psycopg
    from psycopg.types.json import Jsonb


def _conn():
    if IS_PG:
        return psycopg.connect(DB_URL, autocommit=True)
    c = sqlite3.connect(os.environ.get('SQLITE_PATH', 'wa_local.db'), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def q(sql, args=(), fetch=False):
    """Ejecuta SQL escrito con %s; adapta a SQLite cuando hace falta."""
    if not IS_PG:
        sql = sql.replace('%s', '?')
        args = tuple(json.dumps(a, ensure_ascii=False) if isinstance(a, (dict, list)) else a for a in args)
    else:
        args = tuple(Jsonb(a) if isinstance(a, (dict, list)) else a for a in args)
    with _lock:
        c = _conn()
        try:
            cur = c.execute(sql, args)
            rows = cur.fetchall() if fetch else None
            if not IS_PG: c.commit()
            if rows is None: return None
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            c.close()


SERIAL = 'BIGSERIAL PRIMARY KEY' if IS_PG else 'INTEGER PRIMARY KEY AUTOINCREMENT'
JSONT = 'JSONB' if IS_PG else 'TEXT'


def _ensure_col(table, col, typ='TEXT'):
    """Agrega una columna si la tabla ya existía sin ella (SQLite y Postgres)."""
    if not IS_PG:
        cols = q(f'PRAGMA table_info({table})', fetch=True) or []
        if col in {r['name'] for r in cols}:
            return
        q(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
        return
    try:
        q(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
    except Exception as e:
        if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower():
            return
        raise



def init_db():
    q(f"""CREATE TABLE IF NOT EXISTS raw_events (
        id {SERIAL}, received_at TEXT NOT NULL, signature_ok INTEGER, payload {JSONT})""")
    # kind: message | status | outbound
    q(f"""CREATE TABLE IF NOT EXISTS wa_events (
        id {SERIAL}, kind TEXT NOT NULL, wamid TEXT, wa_id TEXT, name TEXT, direction TEXT,
        msg_type TEXT, text TEXT, ts TEXT, status TEXT, auto INTEGER DEFAULT 0, source TEXT,
        phone_number_id TEXT, received_at TEXT NOT NULL, data {JSONT})""")
    q("CREATE UNIQUE INDEX IF NOT EXISTS wa_events_uniq ON wa_events (kind, wamid, status)")
    q("CREATE INDEX IF NOT EXISTS wa_events_waid ON wa_events (wa_id, id)")
    q("""CREATE TABLE IF NOT EXISTS mensajes_last_seen (
        user_slug TEXT NOT NULL, wa_id TEXT NOT NULL, seen_at TEXT NOT NULL,
        PRIMARY KEY (user_slug, wa_id))""")
    q(f"""CREATE TABLE IF NOT EXISTS asesor_clientes (
        id {SERIAL}, asesor_slug TEXT NOT NULL, nombre TEXT NOT NULL, telefono TEXT NOT NULL,
        operacion TEXT, tipo TEXT, zona TEXT, presupuesto TEXT, origen_nota TEXT,
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    q("CREATE UNIQUE INDEX IF NOT EXISTS asesor_clientes_uniq ON asesor_clientes (asesor_slug, telefono)")
    q("CREATE INDEX IF NOT EXISTS asesor_clientes_slug ON asesor_clientes (asesor_slug, id)")
    BLOBT = 'BYTEA' if IS_PG else 'BLOB'
    q(f"""CREATE TABLE IF NOT EXISTS asesor_perfil (
        asesor_slug TEXT PRIMARY KEY, telefono TEXT, email TEXT, cedula TEXT, bio TEXT, zonas TEXT,
        foto {BLOBT}, foto_mime TEXT, updated_at TEXT)""")
    q(f"""CREATE TABLE IF NOT EXISTS captacion_solicitudes (
        id {SERIAL}, asesor_slug TEXT NOT NULL, accion TEXT NOT NULL, codigo TEXT, titulo TEXT, operacion TEXT,
        tipo TEXT, zona TEXT, precio TEXT, origen TEXT, nota TEXT, estado TEXT NOT NULL, created_at TEXT NOT NULL,
        revisado_por TEXT, revisado_at TEXT, nota_revision TEXT)""")
    _ensure_col('captacion_solicitudes', 'revisado_por')
    _ensure_col('captacion_solicitudes', 'revisado_at')
    _ensure_col('captacion_solicitudes', 'nota_revision')
    q(f"""CREATE TABLE IF NOT EXISTS asesor_visitas (
        id {SERIAL}, asesor_slug TEXT NOT NULL, fecha TEXT, hora TEXT, cliente TEXT NOT NULL, inmueble TEXT,
        nota TEXT, created_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS asesor_ofertas (
        id {SERIAL}, asesor_slug TEXT NOT NULL, tipo TEXT NOT NULL, cliente TEXT NOT NULL, inmueble TEXT,
        monto TEXT, fecha TEXT, nota TEXT, created_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS actividades (
        id {SERIAL}, titulo TEXT NOT NULL, fecha TEXT NOT NULL, hora TEXT, modalidad TEXT NOT NULL,
        enlace_lugar TEXT, destinatarios TEXT NOT NULL, creado_por TEXT, created_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS email_outbox (
        id {SERIAL}, to_slug TEXT, to_addr TEXT, asunto TEXT, cuerpo TEXT, evento TEXT, estado TEXT,
        motivo TEXT, created_at TEXT NOT NULL)""")
    q("""CREATE TABLE IF NOT EXISTS asignaciones_notificadas (
        asesor_slug TEXT NOT NULL, cliente_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (asesor_slug, cliente_id))""")
    # Restore point taken once, before the GHL history import. Never overwritten.
    q("CREATE TABLE IF NOT EXISTS bak_pre_ghl_import_wa_events AS SELECT * FROM wa_events")
    q("CREATE TABLE IF NOT EXISTS bak_pre_ghl_import_raw_events AS SELECT * FROM raw_events")
    q(f"""CREATE TABLE IF NOT EXISTS portal_datos (
        nombre TEXT PRIMARY KEY, datos {JSONT}, actualizado TEXT)""")
    try:
        portales.init(q, IS_PG)  # Mercadeo: crm_portal_links (aditiva)
    except Exception:
        log.exception('portales.init')
    try:
        marketplace.init(q, IS_PG)  # Redes Sociales: crm_marketplace_links + bitácora (aditivas)
    except Exception:
        log.exception('marketplace.init')
    seed_emails_perfil()



def seed_emails_perfil():
    """Correos iniciales desde TABLA_ASESORES_GHL_WASI.csv, config/asesores_whatsapp.json,
    asesores_emails_iniciales.json y PORTAL_ASESORES_EMAILS. Solo rellena perfiles sin correo."""
    datos = {}

    def poner(slug, email):
        slug, email = (slug or '').strip().lower(), (email or '').strip()
        if slug and '@' in email and ' ' not in email and portal_lib.asesor_by_user(slug) and slug not in datos:
            datos[slug] = email

    def slug_por_nombre(nombre):
        n = portal_lib.norm(nombre or '')
        if not n:
            return None
        for a in portal_lib.asesores_meta():
            cand = [a.get('user'), a.get('name'), *(a.get('aliases') or [])]
            if n in {portal_lib.norm(x) for x in cand if x}:
                return a['user']
        return None

    aqui = _Path(__file__).resolve().parent
    raiz = aqui.parent
    for path in (raiz / 'TABLA_ASESORES_GHL_WASI.csv', aqui / 'TABLA_ASESORES_GHL_WASI.csv', aqui / 'data' / 'TABLA_ASESORES_GHL_WASI.csv'):
        if not path.is_file():
            continue
        try:
            with path.open(newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    poner(slug_por_nombre(row.get('nombre_ghl') or row.get('nombre_wasi')), row.get('email'))
        except Exception:
            log.warning('No pude leer correos desde %s', path)

    for path in (raiz / 'config' / 'asesores_whatsapp.json', aqui / 'config' / 'asesores_whatsapp.json'):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            for a in (raw.get('asesores') if isinstance(raw, dict) else raw) or []:
                if isinstance(a, dict):
                    poner(a.get('user') or slug_por_nombre(a.get('nombre') or a.get('name')), a.get('email'))
        except Exception:
            log.warning('No pude leer correos desde %s', path)

    bundled = aqui / 'asesores_emails_iniciales.json'
    if bundled.is_file():
        try:
            for slug, email in (json.loads(bundled.read_text(encoding='utf-8')) or {}).items():
                poner(slug, email)
        except Exception:
            log.warning('asesores_emails_iniciales.json no es JSON válido')

    raw = os.environ.get('PORTAL_ASESORES_EMAILS', '').strip()
    if raw:
        try:
            for slug, email in json.loads(raw).items():
                poner(slug, email)
        except Exception:
            log.warning('PORTAL_ASESORES_EMAILS no es JSON válido')

    n = 0
    for slug, email in datos.items():
        rows = q('SELECT email FROM asesor_perfil WHERE asesor_slug=%s', (slug,), fetch=True) or []
        if not rows:
            q('INSERT INTO asesor_perfil (asesor_slug, email, updated_at) VALUES (%s,%s,%s)', (slug, email, None))
            n += 1
        elif not (rows[0].get('email') or '').strip():
            q('UPDATE asesor_perfil SET email=%s WHERE asesor_slug=%s', (email, slug))
            n += 1
    return n



# ---------------------------------------------------------------- utilidades
def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _secret_matches(given, plain_env, sha_env):
    if not given: return False
    plain, sha = os.environ.get(plain_env), os.environ.get(sha_env)
    if plain: return hmac.compare_digest(given, plain)
    if sha: return hmac.compare_digest(_sha(given), sha.lower())
    return False


def require_read_key():
    h = request.headers.get('Authorization', '')
    if not (h.startswith('Bearer ') and _secret_matches(h[7:].strip(), 'WA_READ_KEY', 'WA_READ_KEY_SHA256')):
        abort(401)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def ts_iso(unix):
    try: return datetime.fromtimestamp(int(unix), timezone.utc).isoformat()
    except Exception: return now_iso()


def message_text(m):
    t = m.get('type')
    if t == 'text': return (m.get('text') or {}).get('body', '')
    if t in ('image', 'video', 'document', 'sticker'): return (m.get(t) or {}).get('caption', '') or ''
    if t == 'button': return (m.get('button') or {}).get('text', '')
    if t == 'interactive':
        i = m.get('interactive') or {}
        r = i.get('button_reply') or i.get('list_reply') or {}
        return r.get('title', '')
    if t == 'reaction': return (m.get('reaction') or {}).get('emoji', '')
    if t == 'location':
        l = m.get('location') or {}
        return f"[ubicación] {l.get('name') or ''} {l.get('address') or ''} {l.get('latitude')},{l.get('longitude')}".strip()
    return ''


def insert_event(**kw):
    cols = ['kind', 'wamid', 'wa_id', 'name', 'direction', 'msg_type', 'text', 'ts', 'status', 'auto',
            'source', 'phone_number_id', 'received_at', 'data']
    vals = [kw.get(c) for c in cols]
    vals[cols.index('received_at')] = vals[cols.index('received_at')] or now_iso()
    conflict = 'ON CONFLICT DO NOTHING' if IS_PG else ''
    verb = 'INSERT' if IS_PG else 'INSERT OR IGNORE'
    q(f"{verb} INTO wa_events ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) {conflict}", vals)


# ---------------------------------------------------------------- rutas
@app.get('/health')
def health():
    try:
        q('SELECT 1', fetch=True); db = 'ok'
    except Exception as e:
        db = f'error: {type(e).__name__}'
    return jsonify(ok=db == 'ok', db=db, storage='postgres' if IS_PG else 'sqlite', started=STARTED,
                   signature_validation=bool(os.environ.get('META_APP_SECRET')),
                   verify_token_set=bool(os.environ.get('WA_VERIFY_TOKEN') or os.environ.get('WA_VERIFY_TOKEN_SHA256')),
                   read_key_set=bool(os.environ.get('WA_READ_KEY') or os.environ.get('WA_READ_KEY_SHA256')),
                   auto_reply=os.environ.get('AUTO_REPLY_ENABLED') == '1')


@app.get('/healthz')
def healthz():
    return 'ok'


@app.get('/webhook')
def verify():
    mode = request.args.get('hub.mode'); tok = request.args.get('hub.verify_token'); ch = request.args.get('hub.challenge', '')
    if mode == 'subscribe' and _secret_matches(tok, 'WA_VERIFY_TOKEN', 'WA_VERIFY_TOKEN_SHA256'):
        log.info('Webhook verificado por Meta')
        return ch, 200, {'Content-Type': 'text/plain'}
    log.warning('Verificación de webhook rechazada (mode=%s)', mode)
    return 'forbidden', 403


@app.post('/webhook')
def receive():
    raw = request.get_data()
    secret = os.environ.get('META_APP_SECRET')
    sig_ok = None
    if secret:
        given = request.headers.get('X-Hub-Signature-256', '')
        exp = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(given, exp):
            log.warning('Firma X-Hub-Signature-256 inválida: evento descartado')
            return 'invalid signature', 403
        sig_ok = 1
    else:
        log.warning('META_APP_SECRET no configurado: evento aceptado SIN validar firma')
    try:
        body = json.loads(raw or b'{}')
    except Exception:
        return 'bad json', 400
    q('INSERT INTO raw_events (received_at, signature_ok, payload) VALUES (%s,%s,%s)', (now_iso(), sig_ok, body))
    n_msg = n_st = 0
    for entry in body.get('entry') or []:
        for ch in entry.get('changes') or []:
            v = ch.get('value') or {}
            pnid = (v.get('metadata') or {}).get('phone_number_id')
            names = {c.get('wa_id'): (c.get('profile') or {}).get('name') for c in v.get('contacts') or []}
            for m in v.get('messages') or []:
                insert_event(kind='message', wamid=m.get('id'), wa_id=m.get('from'), name=names.get(m.get('from')),
                             direction='inbound', msg_type=m.get('type'), text=message_text(m),
                             ts=ts_iso(m.get('timestamp')), status='', source='meta', phone_number_id=pnid, data=m)
                n_msg += 1
                maybe_auto_reply(m.get('from'), pnid)
            for s in v.get('statuses') or []:
                insert_event(kind='status', wamid=s.get('id'), wa_id=s.get('recipient_id'), direction='outbound',
                             ts=ts_iso(s.get('timestamp')), status=s.get('status') or '', source='meta',
                             phone_number_id=pnid, data=s)
                n_st += 1
    log.info('Evento guardado: %d mensajes, %d estados', n_msg, n_st)
    return 'EVENT_RECEIVED', 200


@app.get('/api/events')
def api_events():
    require_read_key()
    since = int(request.args.get('since_id', 0)); limit = min(int(request.args.get('limit', 500)), 2000)
    rows = q('SELECT * FROM wa_events WHERE id > %s ORDER BY id LIMIT %s', (since, limit), fetch=True)
    for r in rows:
        if isinstance(r.get('data'), str):
            try: r['data'] = json.loads(r['data'])
            except Exception: pass
    return jsonify(events=rows, last_id=rows[-1]['id'] if rows else since)


@app.post('/api/outbound')
def api_outbound():
    require_read_key()
    b = request.get_json(force=True) or {}
    if not b.get('wa_id'): abort(400)
    insert_event(kind='outbound', wamid=b.get('wamid') or f"local-{time.time_ns()}", wa_id=b['wa_id'],
                 direction='outbound', msg_type='text', text=b.get('text', ''), ts=b.get('ts') or now_iso(),
                 status='', auto=1 if b.get('auto') else 0, source=b.get('source') or 'box', data=b)
    return jsonify(ok=True)


@app.get('/api/last_inbound')
def api_last_inbound():
    require_read_key()
    wa = request.args.get('wa_id', '')
    rows = q("SELECT id, wamid, ts, name, text FROM wa_events WHERE kind='message' AND wa_id=%s ORDER BY ts DESC LIMIT 1",
             (wa,), fetch=True)
    return jsonify(last_inbound=rows[0] if rows else None)


# ---------------------------------------------------------------- panel de entrada (solo lectura)
PANEL_USER = 'jose'
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
         'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')
MESES_CORTOS = ('ene', 'feb', 'mar', 'abr', 'may', 'jun',
                'jul', 'ago', 'sep', 'oct', 'nov', 'dic')
TIPO_ETIQUETA = {
    'text': '', 'image': 'Imagen', 'audio': 'Audio', 'video': 'Video',
    'document': 'Documento', 'sticker': 'Sticker', 'location': 'Ubicación',
    'reaction': 'Reacción', 'button': 'Botón', 'interactive': 'Interactivo',
    'contacts': 'Contacto', 'unsupported': 'No soportado',
}
STATUS_ETIQUETA = {
    'sent': 'Enviado', 'delivered': 'Entregado', 'read': 'Leído',
    'failed': 'Falló', 'deleted': 'Eliminado',
}


LOGIN_FAIL_WINDOW = 15 * 60
LOGIN_FAIL_MAX = 5
_login_fails = {}


def _client_ip():
    xf = request.headers.get('X-Forwarded-For', '')
    if xf:
        return xf.split(',')[0].strip()
    return request.remote_addr or '0.0.0.0'


def _login_fail_keys(username=''):
    keys = ['ip:' + _client_ip()]
    user = (username or '').strip().lower()
    if user:
        keys.append('u:' + user)
    return keys


def _too_many_login_fails(username=''):
    now = time.time()
    with _lock:
        for key in _login_fail_keys(username):
            hits = [t for t in _login_fails.get(key, []) if now - t < LOGIN_FAIL_WINDOW]
            _login_fails[key] = hits
            if len(hits) >= LOGIN_FAIL_MAX:
                return True
    return False


def _record_login_fail(username=''):
    now = time.time()
    with _lock:
        for key in _login_fail_keys(username):
            _login_fails.setdefault(key, []).append(now)


def _clear_login_fails(username=''):
    with _lock:
        for key in _login_fail_keys(username):
            _login_fails.pop(key, None)


def _safe_next(val):
    val = (val or '').strip()
    if not val.startswith('/') or val.startswith('//') or '\\' in val:
        return '/portal'
    path = val.split('?', 1)[0]
    if path.startswith(('/login', '/logout', '/webhook', '/api/', '/static/', '/health')):
        return '/portal'
    return val


def _current_user():
    return portal_lib.user_by_username(session.get('uid'))


def _login_redirect(next_url=None):
    nxt = _safe_next(next_url or (request.full_path if request.query_string else request.path))
    if nxt in ('/', '/portal'):
        return redirect('/login')
    return redirect('/login?next=' + quote(nxt, safe='/'))


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            if request.path == '/panel/data' or request.accept_mimetypes.best == 'application/json':
                return _panel_headers(make_response(jsonify({'error': 'auth'}), 401))
            return _login_redirect()
        g.user = user
        if user.get('role') == 'mercadeo' and request.endpoint not in MERCADEO_ENDPOINTS:
            # Mercadeo solo entra a su sección y al planificador (clientes, mensajes, cierres, etc. quedan fuera).
            if request.method == 'GET' and request.endpoint == 'portal':
                return redirect('/mercadeo')
            return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
        return fn(*args, **kwargs)
    return wrapper


# Rutas con sesión que el usuario de Mercadeo puede abrir. Todo lo demás le responde 403 (en el servidor).
MERCADEO_ENDPOINTS = {'mercadeo', 'mercadeo_link', 'mercadeo_verificar', 'mercadeo_planificador'}
ROLES_MERCADEO = ('admin', 'mercadeo')


def require_roles(*roles):
    """Como require_admin, pero con una lista de roles permitidos (se valida en el servidor)."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.user.get('role') not in roles:
                return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
            return fn(*args, **kwargs)
        return require_login(wrapper)
    return deco


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if g.user.get('role') != 'admin':
            return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
        return fn(*args, **kwargs)
    return require_login(wrapper)


ROLE_LABEL = {'admin': 'Presidencia', 'lider': 'Líder comercial', 'asesor': 'Asesora', 'mercadeo': 'Mercadeo'}


@app.context_processor
def _portal_nav():
    u = getattr(g, 'user', None)
    if not u:
        return {'nav_main': [], 'nav_more': [], 'nav_more_keys': [], 'viewer': None, 'role_label': ''}
    role = u['role']
    if role == 'mercadeo':
        main = [{'href': '/mercadeo', 'label': 'Mercadeo', 'key': 'mercadeo', 'icon': 'megaphone'},
                {'href': '/mercadeo/planificador', 'label': 'Planificador de anuncios', 'short': 'Planificador',
                 'key': 'planificador', 'icon': 'calendar'}]
        return {'nav_main': main, 'nav_more': [], 'nav_more_keys': [], 'viewer': u, 'role_label': ROLE_LABEL['mercadeo']}
    links = _portal_links()
    main = [{'href': '/portal', 'label': 'Inicio', 'key': 'portal', 'icon': 'home'},
            {'href': '/mis-clientes', 'label': 'Mis Clientes', 'short': 'Clientes', 'key': 'clientes', 'icon': 'users'}]
    if role == 'admin':
        main.append({'href': '/inventario', 'label': 'Inventario General', 'short': 'Inventario', 'key': 'inventario', 'icon': 'building'})
        main.append({'href': '/panel', 'label': 'Mensajes', 'key': 'mensajes', 'icon': 'chat'})
    else:
        main.append({'href': '/panel', 'label': 'Mensajes', 'key': 'mensajes', 'icon': 'chat'})
        main.append({'href': '/visitas', 'label': 'Visitas', 'key': 'visitas', 'icon': 'calendar'})
    more = []
    if role != 'admin':
        more.append({'href': '/mis-captaciones', 'label': 'Mis Captaciones', 'key': 'captaciones', 'icon': 'key'})
        more.append({'href': '/perfil', 'label': 'Mi Perfil', 'key': 'perfil', 'icon': 'user'})
    if role in ('admin', 'lider'):
        more.append({'href': '/mi-equipo', 'label': 'Mi Equipo' if role == 'lider' else 'Equipos', 'key': 'equipo', 'icon': 'users'})
        more.append({'href': '/aprobaciones', 'label': 'Aprobaciones', 'key': 'aprobaciones', 'icon': 'clipboard'})
    if role == 'admin':
        more.append({'href': '/mis-captaciones', 'label': 'Mis Captaciones', 'key': 'captaciones', 'icon': 'key'})
    else:
        more.append({'href': '/inventario', 'label': 'Inventario General', 'key': 'inventario', 'icon': 'building'})
    more.append({'href': '/mercadeo', 'label': 'Mercadeo', 'key': 'mercadeo', 'icon': 'megaphone'})
    if role == 'admin':
        more.append({'href': '/mercadeo/planificador', 'label': 'Planificador de anuncios', 'key': 'planificador', 'icon': 'calendar'})
    more.append({'href': '/ofertas', 'label': 'Ofertas', 'key': 'ofertas', 'icon': 'tag'})
    more.append({'href': '/cierres', 'label': 'Cierres', 'key': 'cierres', 'icon': 'cash'})
    if role == 'admin':
        more.append({'href': '/admin/actividades', 'label': 'Actividades', 'key': 'actividades', 'icon': 'calendar'})
        more.append({'href': '/admin/correos', 'label': 'Correos', 'key': 'correos', 'icon': 'mail'})
        if links['cierres_url']:
            more.append({'href': links['cierres_url'], 'label': 'Sistema Contable', 'key': 'contable', 'icon': 'cash', 'external': True})
        if links['estatus_url']:
            more.append({'href': links['estatus_url'], 'label': 'Sistema de Clientes', 'key': 'estatus', 'icon': 'clipboard', 'external': True})
    else:
        more.append({'href': '/actividades', 'label': 'Actividades', 'key': 'actividades', 'icon': 'calendar'})
    if role in ('admin', 'lider'):
        more.append({'href': '/clientes-propios', 'label': 'Clientes propios' if role == 'admin' else 'Clientes del equipo',
                     'key': 'propios_more', 'icon': 'users'})
    return {'nav_main': main, 'nav_more': more, 'nav_more_keys': [m['key'] for m in more],
            'viewer': u, 'role_label': ROLE_LABEL.get(role, role)}


def _parse_caracas(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(CARACAS)
    except Exception:
        return None


def _fmt_time(iso):
    dt = _parse_caracas(iso)
    if not dt:
        return ''
    h = dt.hour % 12 or 12
    ap = 'a. m.' if dt.hour < 12 else 'p. m.'
    clock = f'{h}:{dt.strftime("%M")} {ap}'
    now = datetime.now(CARACAS)
    if dt.date() == now.date():
        return clock
    if dt.year == now.year:
        return f'{dt.day} {MESES_CORTOS[dt.month - 1]}, {clock}'
    return f'{dt.day} {MESES_CORTOS[dt.month - 1]} {dt.year}, {clock}'


def _fmt_day(iso):
    dt = _parse_caracas(iso)
    if not dt:
        return ''
    now = datetime.now(CARACAS)
    if dt.date() == now.date():
        return 'Hoy'
    if dt.date() == now.date() - timedelta(days=1):
        return 'Ayer'
    if dt.year == now.year:
        return f'{dt.day} de {MESES[dt.month - 1]}'
    return f'{dt.day} de {MESES[dt.month - 1]} de {dt.year}'


def _fmt_phone(wa_id):
    d = ''.join(c for c in (wa_id or '') if c.isdigit())
    if d.startswith('58') and len(d) >= 12:
        return f'+58 {d[2:5]}-{d[5:]}'
    if d:
        return '+' + d
    return wa_id or ''


def _event_ts(row):
    ts = row.get('ts') or ''
    rec = row.get('received_at') or ''
    # Algunos eventos de prueba llegan con un ts centinela (epoch / 2026-01-01 00:00Z).
    if ts.startswith(('1970-01-01', '2026-01-01T00:00:00')):
        return rec or ts
    return ts or rec


def _display_name(wa_id, fallback=None):
    return (fallback or '').strip() or _fmt_phone(wa_id) or wa_id or 'Desconocido'


def _preview(row):
    tipo = TIPO_ETIQUETA.get(row.get('msg_type') or '', '')
    text = (row.get('text') or '').strip() or (tipo and f'[{tipo}]') or 'Mensaje'
    if row.get('direction') == 'outbound':
        return 'Tú: ' + text
    return text


def _names_by_wa():
    rows = q(
        """SELECT wa_id, name FROM (
               SELECT wa_id, name,
                      ROW_NUMBER() OVER (PARTITION BY wa_id ORDER BY id DESC) AS rn
               FROM wa_events
               WHERE name IS NOT NULL AND name <> ''
           ) t WHERE rn = 1""",
        fetch=True,
    ) or []
    return {r['wa_id']: r['name'] for r in rows}


def _last_seen_map(user):
    rows = q('SELECT wa_id, seen_at FROM mensajes_last_seen WHERE user_slug=%s', (user.get('user') or '',), fetch=True) or []
    return {r['wa_id']: r['seen_at'] or '' for r in rows}


def _marcar_visto(user, wa_id):
    slug, wa, now = user.get('user') or '', wa_id, now_iso()
    if IS_PG:
        q("""INSERT INTO mensajes_last_seen (user_slug, wa_id, seen_at) VALUES (%s,%s,%s)
             ON CONFLICT (user_slug, wa_id) DO UPDATE SET seen_at=EXCLUDED.seen_at""", (slug, wa, now))
    else:
        q("INSERT OR REPLACE INTO mensajes_last_seen (user_slug, wa_id, seen_at) VALUES (%s,%s,%s)", (slug, wa, now))


def _conteo_no_leidos(user, wa_ids):
    """Inbound después del último outbound o del last_seen del usuario."""
    if not wa_ids:
        return {}
    seen = _last_seen_map(user)
    outs = q(
        """SELECT wa_id, MAX(COALESCE(ts, received_at)) AS t
           FROM wa_events
           WHERE kind IN ('message','outbound') AND direction='outbound'
             AND wa_id IS NOT NULL
           GROUP BY wa_id""",
        fetch=True,
    ) or []
    last_out = {r['wa_id']: r['t'] or '' for r in outs}
    ins = q(
        """SELECT wa_id, COALESCE(ts, received_at) AS t
           FROM wa_events
           WHERE kind IN ('message','outbound') AND direction='inbound'
             AND wa_id IS NOT NULL""",
        fetch=True,
    ) or []
    counts = {w: 0 for w in wa_ids}
    for r in ins:
        wa = r['wa_id']
        if wa not in counts:
            continue
        corte = max(last_out.get(wa) or '', seen.get(wa) or '')
        if (r['t'] or '') > corte:
            counts[wa] += 1
    return counts


def panel_conversations(user):
    latest = q(
        """SELECT wa_id, name, text, ts, received_at, direction, msg_type, source, data FROM (
               SELECT wa_id, name, text, ts, received_at, direction, msg_type, source, data,
                      ROW_NUMBER() OVER (
                          PARTITION BY wa_id
                          ORDER BY COALESCE(ts, received_at) DESC, id DESC
                      ) AS rn
               FROM wa_events
               WHERE wa_id IS NOT NULL AND wa_id <> ''
                 AND kind IN ('message', 'outbound')
           ) t WHERE rn = 1""",
        fetch=True,
    ) or []
    names = _names_by_wa()
    visibles = mensajes.telefonos_visibles_de(user, db_propios=_db_propios)
    ocultos = mensajes.telefonos_ocultos()
    out = []
    for r in latest:
        wa = r['wa_id']
        if not mensajes.puede_ver(user, wa, visibles=visibles, ocultos=ocultos):
            continue
        nombre = _display_name(wa, names.get(wa) or r.get('name'))
        canal = mensajes.canal_de(r)
        out.append({
            'wa_id': wa,
            'href': '/panel?c=' + quote(wa, safe=''),
            'name': nombre,
            'phone': _fmt_phone(wa),
            'preview': _preview(r),
            'time': mensajes.hora_lista(_event_ts(r), _parse_caracas),
            'time_full': _fmt_time(_event_ts(r)),
            'sort': _event_ts(r),
            'canal': canal,
            'canal_label': mensajes.canal_etiqueta(canal),
            'iniciales': mensajes.iniciales(nombre, wa),
            'color': mensajes.color_avatar(nombre or wa),
            'unread': 0,
        })
    counts = _conteo_no_leidos(user, [c['wa_id'] for c in out])
    for c in out:
        c['unread'] = counts.get(c['wa_id'] or '', 0)
    out.sort(key=lambda x: x['sort'] or '', reverse=True)
    for c in out:
        c['_sort'] = c.pop('sort', None)
    return out


def panel_thread(wa_id):
    rows = q(
        """SELECT id, kind, wamid, wa_id, name, direction, msg_type, text, ts, status, auto, source, received_at
           FROM wa_events
           WHERE wa_id=%s AND kind IN ('message', 'outbound', 'status')
           ORDER BY COALESCE(ts, received_at) ASC, id ASC""",
        (wa_id,),
        fetch=True,
    ) or []
    statuses = {}
    for r in rows:
        if r.get('kind') == 'status' and r.get('wamid') and r.get('status'):
            statuses[r['wamid']] = r['status']
    names = _names_by_wa()
    msgs = []
    last_day = None
    for r in rows:
        if r.get('kind') not in ('message', 'outbound'):
            continue
        iso = _event_ts(r)
        day = _fmt_day(iso)
        typ = r.get('msg_type') or ''
        typ_label = TIPO_ETIQUETA.get(typ, typ if typ and typ != 'text' else '')
        body = (r.get('text') or '').strip() or (typ_label and f'[{typ_label}]') or 'Mensaje'
        st_raw = (r.get('status') or '') or statuses.get(r.get('wamid') or '', '')
        st_label = STATUS_ETIQUETA.get(st_raw, st_raw)
        if st_raw == 'read':
            st_class = 'leido'
        elif st_raw in ('failed', 'deleted'):
            st_class = 'fallo'
        else:
            st_class = ''
        msgs.append({
            'direction': 'out' if r.get('direction') == 'outbound' else 'in',
            'text': body,
            'type_label': typ_label if typ != 'text' else '',
            'status': st_label,
            'status_class': st_class,
            'time': _fmt_time(iso),
            'day': day if day != last_day else '',
        })
        last_day = day
    thread_name = _display_name(wa_id, names.get(wa_id) or next((r.get('name') for r in rows if r.get('name')), None))
    return msgs, thread_name, _fmt_phone(wa_id)


def _ultimo_entrante(wa_id):
    rows = q("""SELECT ts, received_at FROM wa_events
                WHERE kind='message' AND direction='inbound' AND wa_id=%s
                ORDER BY COALESCE(ts, received_at) DESC, id DESC LIMIT 1""", (wa_id,), fetch=True) or []
    return _event_ts(rows[0]) if rows else ''


def _telefonos_asesores():
    """Teléfonos de asesores (perfil) para el filtro: nunca se comparten con clientes."""
    try:
        rows = q("SELECT telefono FROM asesor_perfil WHERE telefono IS NOT NULL AND telefono <> ''", fetch=True) or []
    except Exception:
        rows = []
    return [r['telefono'] for r in rows]


def _social_convos(user):
    """IG/FB: solo admin y solo con META_SOCIAL_INBOX=1 (lectura por Graph API)."""
    if user.get('role') != 'admin' or not bandeja.social_activo():
        return []
    out = []
    for c in bandeja.social_conversaciones():
        nombre = c['nombre']
        out.append({
            'wa_id': c['id'], 'href': '/panel?c=' + quote(c['id'], safe=''), 'name': nombre, 'phone': '',
            'preview': ('Tú: ' if c['saliente'] else '') + (c['texto'] or 'Mensaje'),
            'time': mensajes.hora_lista(c['ts'], _parse_caracas), 'time_full': _fmt_time(c['ts']),
            'canal': c['canal'], 'canal_label': mensajes.canal_etiqueta(c['canal']),
            'iniciales': mensajes.iniciales(nombre), 'color': mensajes.color_avatar(nombre), 'unread': 0,
            '_sort': c['ts'],
        })
    return out


def _es_social(conv_id):
    return (conv_id or '').startswith(('ig:', 'fb:'))


def _panel_payload(user, wa_id, *, marcar=False):
    current = wa_id or ''
    social = _es_social(current)
    if current and social and (user.get('role') != 'admin' or not bandeja.social_activo()):
        return None
    if current and not social and not mensajes.puede_ver(user, current):
        return None
    if current and marcar and not social:
        _marcar_visto(user, current)
    convos = panel_conversations(user)
    convos = _mezclar(convos, _social_convos(user))
    if social and current:
        hilo = bandeja.social_hilo(current) or {'canal': 'instagram' if current.startswith('ig:') else 'facebook', 'mensajes': []}
        msgs, last_day = [], None
        for m in hilo['mensajes']:
            day = _fmt_day(m['ts'])
            msgs.append({'direction': 'out' if m['saliente'] else 'in', 'text': m['texto'] or 'Mensaje',
                         'type_label': '', 'status': '', 'status_class': '', 'time': _fmt_time(m['ts']),
                         'day': day if day != last_day else ''})
            last_day = day
        cur = next((c for c in convos if c['wa_id'] == current), {})
        name, phone = cur.get('name') or 'Cliente', cur.get('canal_label') or ''
        reply = bandeja.estado_respuesta(hilo['canal'], '')
    else:
        msgs, name, phone = panel_thread(current) if current else ([], '', '')
        reply = bandeja.estado_respuesta('whatsapp', _ultimo_entrante(current)) if current else None
    if reply and reply.get('vence'):
        reply['vence_txt'] = _fmt_time(reply['vence'])
    return {
        'conversations': convos,
        'current': current,
        'messages': msgs,
        'thread_name': name,
        'thread_phone': phone,
        'solo_lectura': not (reply and reply.get('puede_enviar')),
        'reply': reply,
        'es_admin': user.get('role') == 'admin',
        'csrf': portal_lib.csrf_token(user),
    }


def _mezclar(wa, social):
    """Une WhatsApp con IG/FB por fecha del último mensaje (más reciente primero)."""
    def k(c):
        dt = _parse_caracas(c.get('_sort') or '')
        return dt.timestamp() if dt else 0
    todos = sorted(list(wa) + list(social), key=k, reverse=True)
    for c in todos:
        c.pop('_sort', None)
    return todos


def _panel_headers(resp):
    resp = make_response(resp)
    resp.headers['Cache-Control'] = 'private, no-store'
    resp.headers['X-Frame-Options'] = 'DENY'
    return resp


@app.get('/panel')
@require_login
def panel():
    wa = request.args.get('c') or ''
    data = _panel_payload(g.user, wa, marcar=bool(wa))
    if data is None:
        return _panel_headers(Response('No tienes acceso a esta conversación.\n', 403))
    return _panel_headers(render_template('panel.html', **data))


@app.get('/panel/data')
@require_login
def panel_data():
    wa = request.args.get('c') or ''
    data = _panel_payload(g.user, wa, marcar=bool(wa))
    if data is None:
        return _panel_headers(Response('No tienes acceso a esta conversación.\n', 403))
    return _panel_headers(jsonify(data))


@app.post('/panel/responder')
@require_login
def panel_responder():
    """Responder por WhatsApp desde la bandeja. Todo se valida aquí (no en la pantalla):
    sesión, CSRF, que el usuario pueda ver la conversación, ventana de 24 h, filtro de texto y límite por minuto."""
    b = request.get_json(silent=True) if request.is_json else None
    f = b if isinstance(b, dict) else request.form
    quiere_json = request.is_json or request.accept_mimetypes.best == 'application/json'
    wa = (f.get('c') or '').strip()

    def resp(ok, msg, status):
        if quiere_json:
            return _panel_headers(make_response(jsonify({'ok': ok, 'mensaje': msg}), status))
        destino = '/panel?c=' + quote(wa, safe='') + ('&env=1' if ok else '&err=' + quote(msg, safe=''))
        return redirect(destino if wa else '/panel')

    if not portal_lib.csrf_ok(g.user, f.get('csrf') or request.headers.get('X-CSRF-Token')):
        return resp(False, 'La sesión expiró. Recarga la página.', 400)
    if not wa:
        return resp(False, 'Falta la conversación.', 400)
    if _es_social(wa):
        return resp(False, bandeja.estado_respuesta('instagram', '')['motivo'], 403)
    if not mensajes.puede_ver(g.user, wa):
        return resp(False, 'No tienes acceso a esta conversación.', 403)
    est = bandeja.estado_respuesta('whatsapp', _ultimo_entrante(wa))
    if not est['puede_enviar']:
        return resp(False, est['motivo'], 409)
    try:
        texto = bandeja.revisar_texto(f.get('texto'), _telefonos_asesores())
    except ValueError as e:
        return resp(False, str(e), 400)
    if not bandeja.limite_ok(g.user['user']):
        return resp(False, 'Demasiados envíos seguidos. Espera un minuto.', 429)
    try:
        wamid = bandeja.enviar_wa(wa, texto)
    except bandeja.EnvioError as e:
        log.warning('Respuesta desde el portal rechazada (%s, código %s)', g.user['user'], e.codigo)
        return resp(False, str(e), 502)
    insert_event(kind='outbound', wamid=wamid, wa_id=wa, direction='outbound', msg_type='text', text=texto,
                 ts=now_iso(), status='', auto=0, source='portal', phone_number_id=os.environ.get('WA_PHONE_NUMBER_ID'),
                 data={'por': g.user['user'], 'canal': 'whatsapp'})
    _marcar_visto(g.user, wa)
    log.info('Respuesta enviada desde el portal por %s', g.user['user'])
    return resp(True, 'Mensaje enviado.', 200)


def _table_count(table, where='', args=()):
    try:
        sql = 'SELECT COUNT(*) AS n FROM ' + table
        if where:
            sql += ' WHERE ' + where
        rows = q(sql, args, fetch=True) or []
        return int(rows[0]['n']) if rows else 0
    except Exception:
        return None


def _already_live_event(wa_id, kind, text, ts, msg_type):
    """True if a live (non-import) event already represents this message. Does not mutate it."""
    rows = q(
        """SELECT text, ts, msg_type FROM wa_events
           WHERE wa_id=%s AND kind=%s AND kind IN ('message','outbound')
             AND (source IS NULL OR source <> %s)""",
        (wa_id, kind, 'ghl_import'),
        fetch=True,
    ) or []
    minute = (ts or '')[:16].replace('Z', '')
    text = (text or '').strip()
    for x in rows:
        same_text = (x.get('text') or '').strip() == text
        same_min = (x.get('ts') or '')[:16].replace('Z', '') == minute
        if text and same_text and same_min:
            return True
        if (not text) and same_min and (x.get('msg_type') or '') == (msg_type or ''):
            return True
    return False


# Importación HISTÓRICA de conversaciones GHL (source='ghl_import', wamid 'ghl:…'). Es un dato ya guardado,
# no una conexión: no llama a GHL. Tras el corte a Meta y el borrado de GHL queda como consulta/recarga del histórico.
@app.get('/panel/import-ghl')
@require_admin
def panel_import_ghl_status():
    st = {
        'backup_wa_events': _table_count('bak_pre_ghl_import_wa_events'),
        'backup_raw_events': _table_count('bak_pre_ghl_import_raw_events'),
        'wa_events': _table_count('wa_events'),
        'ghl_import': _table_count('wa_events', 'source=%s', ('ghl_import',)),
        'ghl_conversations': _table_count(
            'wa_events', "source=%s AND kind IN ('message','outbound')", ('ghl_import',)
        ),
    }
    # distinct conversations
    try:
        rows = q(
            "SELECT COUNT(DISTINCT wa_id) AS n FROM wa_events WHERE source=%s",
            ('ghl_import',),
            fetch=True,
        ) or []
        st['ghl_conversations'] = int(rows[0]['n']) if rows else 0
    except Exception:
        pass
    return _panel_headers(jsonify(ok=True, **st))


@app.post('/panel/import-ghl')
@require_admin
def panel_import_ghl():
    """Load GHL history rows (source=ghl_import, wamid ghl:...). Re-runnable. Admin only."""
    q("CREATE TABLE IF NOT EXISTS bak_pre_ghl_import_wa_events AS SELECT * FROM wa_events")
    q("CREATE TABLE IF NOT EXISTS bak_pre_ghl_import_raw_events AS SELECT * FROM raw_events")
    body = request.get_json(silent=True, force=True) or {}
    rows = body.get('rows') or []
    inserted = skipped_conflict = skipped_live = rejected = 0
    for r in rows:
        if not isinstance(r, dict):
            rejected += 1
            continue
        wamid = str(r.get('wamid') or '')
        if not wamid.startswith('ghl:'):
            rejected += 1
            continue
        r = dict(r)
        r['source'] = 'ghl_import'
        if _already_live_event(r.get('wa_id') or '', r.get('kind') or '', r.get('text') or '', r.get('ts') or '', r.get('msg_type') or ''):
            skipped_live += 1
            continue
        before = q("SELECT 1 FROM wa_events WHERE kind=%s AND wamid=%s AND coalesce(status,'')=coalesce(%s,'') LIMIT 1",
                   (r.get('kind'), wamid, r.get('status') or ''), fetch=True)
        insert_event(
            kind=r.get('kind'), wamid=wamid, wa_id=r.get('wa_id'), name=r.get('name'),
            direction=r.get('direction'), msg_type=r.get('msg_type'), text=r.get('text') or '',
            ts=r.get('ts'), status=r.get('status') or '', auto=int(r.get('auto') or 0),
            source='ghl_import', phone_number_id=r.get('phone_number_id') or '',
            received_at=r.get('received_at'), data=r.get('data') or {},
        )
        after = q("SELECT 1 FROM wa_events WHERE kind=%s AND wamid=%s AND coalesce(status,'')=coalesce(%s,'') LIMIT 1",
                  (r.get('kind'), wamid, r.get('status') or ''), fetch=True)
        if before:
            skipped_conflict += 1
        elif after:
            inserted += 1
        else:
            rejected += 1
    st = {
        'ok': True,
        'received': len(rows),
        'inserted': inserted,
        'skipped_conflict': skipped_conflict,
        'skipped_live': skipped_live,
        'rejected': rejected,
        'backup_wa_events': _table_count('bak_pre_ghl_import_wa_events'),
        'ghl_import': _table_count('wa_events', 'source=%s', ('ghl_import',)),
    }
    try:
        rows_n = q("SELECT COUNT(DISTINCT wa_id) AS n FROM wa_events WHERE source=%s", ('ghl_import',), fetch=True) or []
        st['ghl_conversations'] = int(rows_n[0]['n']) if rows_n else 0
    except Exception:
        st['ghl_conversations'] = None
    return _panel_headers(jsonify(st))


INV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'inventario.json')


def _inventario():
    live = portal_lib._load('inventario.json', None) if portal_lib._LIVE_LOADER else None
    if isinstance(live, dict) and isinstance(live.get('grupos'), dict):
        return live
    try:
        with open(INV_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'actualizado': '', 'grupos': {'propio': [], 'alianza': [], 'red': []}, 'conteos': {}}


_FOTO_HOSTS = (
    'image.wasi.co', 'images.wasi.co',
    'http2.mlstatic.com', 'mlstatic.com',
)


def _foto_ok(url):
    if not isinstance(url, str) or not url.startswith('https://'):
        return False
    host = url.split('/')[2].lower()
    return any(host == h or host.endswith('.' + h) for h in _FOTO_HOSTS)


def _fotos_limpias(fotos):
    out, seen = [], set()
    for f in fotos or []:
        if isinstance(f, str):
            full, thumb = f, f
        elif isinstance(f, dict):
            full = f.get('full') or f.get('url') or ''
            thumb = f.get('thumb') or full
        else:
            continue
        if not _foto_ok(full):
            continue
        if not _foto_ok(thumb):
            thumb = full
        if full in seen:
            continue
        seen.add(full)
        out.append({'full': full, 'thumb': thumb})
    return out


def _fotos_indice(data=None):
    """codigo / título+zona+precio -> fotos. Solo Propias y Aliados."""
    data = data or _inventario()
    by_code, by_key = {}, {}
    for g in ('propio', 'alianza'):
        for it in (data.get('grupos') or {}).get(g) or []:
            fotos = _fotos_limpias(it.get('fotos'))
            if not fotos:
                continue
            code = str(it.get('codigo') or '').strip()
            if code and code != '—':
                by_code[code] = fotos
            key = (
                (it.get('titulo') or '').strip().lower(),
                (it.get('zona') or '').strip().lower(),
                (it.get('precio') or '').strip().lower(),
            )
            by_key[key] = fotos
    return by_code, by_key


def _ficha_publica(it, *, fotos_code=None, fotos_key=None):
    """Campos públicos de un inmueble. Sin captador ni enlaces de Rex/Wasi."""
    fotos = _fotos_limpias(it.get('fotos'))
    code = str(it.get('codigo') or '').strip()
    if not fotos and fotos_code is not None:
        if code and code != '—':
            fotos = list(fotos_code.get(code) or [])
        if not fotos and fotos_key is not None:
            key = (
                (it.get('titulo') or '').strip().lower(),
                (it.get('zona') or '').strip().lower(),
                (it.get('precio') or '').strip().lower(),
            )
            fotos = list(fotos_key.get(key) or [])
    return {
        'titulo': it.get('titulo') or '',
        'operacion': it.get('operacion') or '',
        'tipo': it.get('tipo') or '',
        'zona': it.get('zona') or '',
        'precio': it.get('precio') or '',
        'codigo': '' if (not code or code == '—') else code,
        'solo_cliente_directo': bool(it.get('solo_cliente_directo')),
        'fotos': fotos,
    }


def _fichas_publicas(items, data=None):
    code, key = _fotos_indice(data)
    return [_ficha_publica(it, fotos_code=code, fotos_key=key) for it in items or []]


def _fmt_actualizado(iso):
    dt = _parse_caracas(iso)
    if not dt:
        return iso or 'fecha no disponible'
    h = dt.hour % 12 or 12
    ap = 'a. m.' if dt.hour < 12 else 'p. m.'
    return f'{dt.day} de {MESES[dt.month - 1]} de {dt.year}, {h}:{dt.strftime("%M")} {ap} (Caracas)'


def _portal_links():
    return {
        'estatus_url': os.environ.get('PORTAL_ESTATUS_URL', '').strip(),
        'cierres_url': os.environ.get('PORTAL_CIERRES_URL', '').strip(),
    }


def _busca(c):
    parts = [x for x in (c.get('operacion'), c.get('tipo'), c.get('zona'), c.get('presupuesto')) if x]
    return ' · '.join(parts)


def _db_propios(slugs, *, todos=False):
    from corte_clientes import en_ejercicio
    rows = []
    for s in slugs:
        rows.extend(q('SELECT * FROM asesor_clientes WHERE asesor_slug=%s ORDER BY id DESC', (s,), fetch=True) or [])
    if todos:
        return rows
    return [r for r in rows if en_ejercicio(created_at=r.get('created_at'), propio=True)]


def _nombre_asesor(slug):
    m = portal_lib.asesor_by_user(slug)
    return (m or {}).get('name') or slug


def _asesor_clientes(user):
    """Lista unificada: propios del asesor (editables) y asignados por For Rent (solo lectura).

    Admin (José): todos los asignados del ejercicio vigente (corte 31-oct-2026),
    con el asesor de cada cliente. Líder y asesora: solo los suyos (filtro en servidor).
    """
    es_admin = user.get('role') == 'admin'
    if es_admin:
        asignados, actualizado, _fuente = portal_lib.load_asignados()
        propios = []
    else:
        mine = user['user']
        asignados, actualizado, _fuente = portal_lib.load_asignados(slug=mine)
        propios = _db_propios([mine])
    out = []
    for r in propios:
        out.append({
            'id': r['id'], 'nombre': r['nombre'], 'telefono': r['telefono'],
            'estatus': r['status'], 'estado': '', 'situacion': '',
            'origen': 'Propio del asesor',
            'origen_nota': r.get('origen_nota') or '',
            'busca': _busca(r), 'inmueble': '', 'editable': True,
        })
    for r in asignados:
        rec = {
            'id': r.get('id'), 'nombre': r.get('nombre') or 'Cliente', 'telefono': '',
            'estatus': r.get('estatus') or 'Sin estatus',
            'estado': r.get('estado') or '', 'situacion': r.get('situacion') or '',
            'origen': 'Asignado por For Rent',
            'origen_nota': '', 'busca': r.get('operacion') or '',
            'inmueble': r.get('inmueble') or '', 'editable': False,
        }
        if es_admin:
            slug = (r.get('asesor') or '').strip()
            rec['asesor'] = slug
            rec['asesor_nombre'] = _nombre_asesor(slug) if slug else ''
        out.append(rec)
    return out, actualizado


def _cierres_vista(user, *, full):
    items = portal_lib.load_cierres(slug=user['user'], full=full)
    for c in items:
        c['fecha_fmt'] = portal_lib.fmt_fecha(c.get('fecha') or '')
        calc = c.get('calc') or {}
        c['asesor_fmt'] = portal_lib.money(calc.get('asesor'))
        c['forrent_fmt'] = portal_lib.money(calc.get('forrent'))
        c['inmobiliaria_fmt'] = portal_lib.money(calc.get('inmobiliaria')) if 'inmobiliaria' in calc else '—'
    return items


def _render_asesor(user, flash_ok='', flash_err='', form=None):
    full = user['role'] in ('admin', 'lider')
    cierres = _cierres_vista(user, full=full)
    clientes, _ = _asesor_clientes(user)
    return _panel_headers(render_template(
        'asesor.html', cierres=cierres, n_cierres=len(cierres), dash=_dashboard(user),
        fases=_fases_de(clientes),
    ))


def _render_clientes(user, flash_ok='', flash_err='', form=None, status=200):
    clientes, cli_fecha = _asesor_clientes(user)
    resp = _panel_headers(render_template(
        'clientes.html',
        clientes=clientes, n_clientes=len(clientes), fases=_fases_de(clientes),
        cli_fecha=portal_lib.fmt_fecha(cli_fecha) if cli_fecha else 'fecha no disponible',
        estatus_opciones=portal_lib.ESTATUS_OPCIONES,
        csrf=portal_lib.csrf_token(user), puede_agregar=user['role'] != 'admin',
        mostrar_asesor=user.get('role') == 'admin',
        flash_ok=flash_ok, flash_err=flash_err, form=form or {},
    ))
    resp.status_code = status
    return resp



def _hoy_caracas():
    return datetime.now(CARACAS).date()


def _fecha_corta(iso):
    dt = _parse_caracas(iso)
    if not dt:
        return 'fecha no disponible'
    return f'{dt.day} {MESES_CORTOS[dt.month - 1]} {dt.year}'


def _fecha_hora(iso):
    dt = _parse_caracas(iso)
    if not dt:
        return 'fecha no disponible'
    h = dt.hour % 12 or 12
    return f"{dt.day} {MESES_CORTOS[dt.month - 1]}, {h}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"


def _kpi(label, value=None, meta='', href=''):
    return {'label': label, 'value': value, 'meta': meta, 'href': href}


def _fases_de(clientes):
    return portal_lib.resumen_fases(clientes)


def _clientes_dashboard(user):
    """Mismo alcance que el dashboard: José ve a todos; líder y asesora, los suyos."""
    if user['role'] != 'admin':
        return _asesor_clientes(user)[0]
    asignados, _, _ = portal_lib.load_asignados()
    propios = _db_propios(portal_lib.visible_slugs(user))
    out = []
    for r in propios:
        out.append({'estatus': r.get('status') or 'Sin estatus', 'estado': '', 'situacion': ''})
    for r in asignados:
        out.append({
            'estatus': r.get('estatus') or 'Sin estatus',
            'estado': r.get('estado') or '', 'situacion': r.get('situacion') or '',
        })
    return out


def _dashboard(user):
    """Indicadores reales, filtrados en servidor según rol. None = sin dato (estado vacío)."""
    role = user['role']
    slugs = None if role == 'admin' else [user['user']]
    asig, asig_act, _ = portal_lib.load_asignados(slugs=slugs) if slugs else portal_lib.load_asignados()
    propios = _db_propios(slugs or portal_lib.visible_slugs(user))
    inv = _inventario()
    cap_data = portal_lib._load('captaciones.json', {})
    cap = portal_lib.load_captaciones(slugs=slugs) if slugs else (cap_data.get('items') or [])
    cierres = portal_lib.load_cierres(slugs=slugs, full=(role != 'asesor')) if slugs else portal_lib.load_cierres(full=True)
    ci_act = portal_lib._load('cierres.json', {}).get('actualizado') or ''
    hoy = _hoy_caracas()
    f_asig, f_inv, f_ci = _fecha_hora(asig_act), _fecha_corta(inv.get('actualizado') or ''), _fecha_hora(ci_act)

    def es_hoy(iso):
        dt = _parse_caracas(iso) if iso and ('+' in iso[10:] or iso.endswith('Z')) else None
        if dt:
            return dt.date() == hoy
        return (iso or '')[:10] == hoy.isoformat()

    nuevos = sum(1 for c in asig if es_hoy(c.get('asignado_at') or '')) + sum(1 for r in propios if es_hoy(r.get('created_at') or ''))
    est = [c.get('estatus') for c in asig] + [r.get('status') for r in propios]
    visitas = sum(1 for s in est if s == 'Visita agendada')
    reservas = sum(1 for s in est if s in ('Reserva agendada', 'Reserva firmada'))
    mes = hoy.strftime('%Y-%m')
    cierres_mes = sum(1 for c in cierres if (c.get('fecha') or '').startswith(mes))
    vacio = f'Sin dato todavía · actualizado {f_asig}'
    prod = portal_lib._load('productividad.json', {}) or {}
    pa = prod.get('por_asesor') if isinstance(prod.get('por_asesor'), dict) else None
    p_act = p_cont = p_seg = None
    p_act_meta = p_cont_meta = p_seg_meta = 'Sin dato'
    if pa is not None:
        f_p = _fecha_hora(prod.get('actualizado') or '')
        vis = {k: v for k, v in pa.items() if slugs is None or k in slugs}
        p_cont = sum(v.get('contactados', 0) for v in vis.values())
        p_seg = sum(v.get('seguimientos_7d', 0) for v in vis.values())
        tot = sum(v.get('clientes', 0) for v in vis.values())
        p_cont_meta = f'De {tot} en ejercicio · Sistema de Clientes · {f_p}'
        p_seg_meta = f'Actualizaciones de estatus, últimos 7 días · {f_p}'
        if role == 'asesor':
            p_act = p_seg
            p_act_meta = f'Tus actualizaciones, últimos 7 días · {f_p}'
        else:
            act = sum(1 for v in vis.values() if v.get('seguimientos_7d', 0) > 0)
            p_act = f'{act}/{len(vis)}' if vis else None
            p_act_meta = f'Asesoras con actualizaciones en 7 días · {f_p}' if vis else 'Sin dato'
    mis = 'tuyos' if role != 'admin' else 'del equipo'
    grupos = inv.get('grupos') or {}
    n_prop_inv, n_ali_inv = len(grupos.get('propio') or []), len(grupos.get('alianza') or [])
    n_cap_prop = sum(1 for x in cap if x.get('origen') == 'propio')
    n_cap_ali = sum(1 for x in cap if x.get('origen') == 'alianza')

    secciones = [
        ('Clientes', 'users', [
            _kpi('Nuevos del día', nuevos, f'Asignados hoy (actualizado {f_asig}) + propios registrados hoy'),
            _kpi('Asignados', len(asig), f'{"Tus asignados" if role != "admin" else "Todos"} · actualizado {f_asig}',
                 '/mis-clientes'),
            _kpi('Contactos pendientes', None, vacio),
            _kpi('Seguimientos vencidos', None, vacio),
        ]),
        ('Inventario', 'building', [
            _kpi('Disponibles', n_prop_inv + n_ali_inv, f'Propias + aliados (sin red) · {f_inv}', '/inventario'),
            _kpi('Captaciones propias', n_cap_prop if role != 'admin' else n_prop_inv,
                 (f'Tuyas · {f_inv}' if role != 'admin' else f'De For Rent · {f_inv}'),
                 '/mis-captaciones' if role != 'admin' else '/inventario?f=propias'),
            _kpi('En alianza', n_cap_ali if role != 'admin' else n_ali_inv,
                 (f'Tuyas · {f_inv}' if role != 'admin' else f'Aliados · {f_inv}'),
                 '/mis-captaciones' if role != 'admin' else '/inventario?f=aliados'),
            _kpi('Nuevas incorporadas', None, f'Sin dato todavía · inventario del {f_inv}'),
        ]),
        ('Operaciones', 'calendar', [
            _kpi('Visitas programadas', visitas, f'Estatus «Visita agendada» · actualizado {f_asig}'),
            _kpi('Negociaciones activas', None, vacio),
            _kpi('Reservas', reservas, f'Reserva agendada o firmada · actualizado {f_asig}'),
            _kpi('Cierres del mes', cierres_mes, f'{MESES[hoy.month - 1].capitalize()} · cierres actualizados {f_ci}',
                 '/cierres'),
        ]),
        ('Productividad', 'chart', [
            _kpi('Actividad de las asesoras' if role != 'asesor' else 'Tu actividad', p_act, p_act_meta),
            _kpi('Clientes contactados', p_cont, p_cont_meta, '/mis-clientes'),
            _kpi('Seguimientos realizados', p_seg, p_seg_meta),
            _kpi('Resultados comerciales', None, f'Sin dato todavía · cierres actualizados {f_ci}'),
        ]),
    ]
    return secciones


# ---------------------------------------------------------------- datos vivos del portal
# Clientes, teléfonos, productividad y cierres salen del CRM propio (tablas crm_*, ver crm.portal_payload).
# Inventario y captaciones los empuja la caja (Wasi/Rex, sin GHL) con una llave propia; sin llave, el endpoint
# queda cerrado. El push de clientes queda solo como respaldo si el CRM no responde.
PORTAL_SYNC_NOMBRES = ('clientes_asignados.json', 'cierres.json', 'clientes_telefonos.json',
                       'inventario.json', 'captaciones.json', 'productividad.json', 'planificador.json')
_SYNC_LISTA = {'clientes_asignados.json': 'clientes', 'clientes_telefonos.json': 'clientes', 'cierres.json': 'cierres',
               'inventario.json': 'grupos', 'captaciones.json': 'items', 'productividad.json': 'por_asesor',
               'planificador.json': 'posts'}
_live_cache = {'t': 0.0, 'd': {}}


def _portal_live(nombre):
    if nombre not in PORTAL_SYNC_NOMBRES:
        return None
    # Rama CRM: clientes, teléfonos, productividad y cierres salen de las tablas crm_*.
    # Inventario y captaciones siguen en portal_datos. Si el CRM aún no está cargado, se usa el push.
    if nombre in ('clientes_asignados.json', 'clientes_telefonos.json', 'productividad.json', 'cierres.json'):
        try:
            import crm as _crm
            d = _crm.portal_payload(nombre, q)
            if d is not None:
                return d
        except Exception:
            pass
    import time as _t
    now = _t.time()
    if now - _live_cache['t'] > 30:
        try:
            rows = q('SELECT nombre, datos FROM portal_datos', fetch=True) or []
            d = {}
            for r in rows:
                v = r['datos']
                if isinstance(v, str):
                    v = json.loads(v)
                d[r['nombre']] = v
            _live_cache['d'] = d
        except Exception:
            pass
        _live_cache['t'] = now
    return _live_cache['d'].get(nombre)


portal_lib.set_live_loader(_portal_live)


def _sync_key_ok():
    k = os.environ.get('PORTAL_SYNC_KEY', '')
    g = request.headers.get('X-Portal-Sync-Key', '')
    return bool(k) and hmac.compare_digest(g.encode(), k.encode())


def _sync_resumen():
    out = {}
    for n in PORTAL_SYNC_NOMBRES:
        d = _portal_live(n) or {}
        lista = d.get(_SYNC_LISTA[n])
        if isinstance(lista, dict) and n == 'inventario.json':
            lista = [x for g in lista.values() for x in g]
        out[n] = {'actualizado': d.get('actualizado') or d.get('generado_at') or '', 'n': len(lista or [])}
    return out


@app.post('/admin/portal-sync')
def portal_sync():
    if not os.environ.get('PORTAL_SYNC_KEY'):
        return jsonify({'error': 'sync no configurado'}), 503
    if not _sync_key_ok():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    guardados = {}
    for n in PORTAL_SYNC_NOMBRES:
        d = body.get(n)
        if d is None:
            continue
        key = _SYNC_LISTA[n]
        tipo = dict if key in ('grupos', 'por_asesor') else list
        if not isinstance(d, dict) or not isinstance(d.get(key), tipo):
            return jsonify({'error': f'formato inválido: {n}'}), 400
        prev = _portal_live(n) or portal_lib._load(n, {}) or {}

        def _cuenta(x):
            v = (x or {}).get(key) or ([] if tipo is list else {})
            return sum(len(g) for g in v.values()) if key == 'grupos' else len(v)
        n_new, n_prev = _cuenta(d), _cuenta(prev)
        # Defensa: no aceptar listas vacías o que caigan a menos de la mitad de golpe.
        if key in ('clientes', 'grupos', 'items') and (not n_new or (n_prev and n_new < n_prev // 2)):
            return jsonify({'error': f'{n}: {n_new} filas contra {n_prev}; rechazado'}), 409
        act = d.get('actualizado') or d.get('generado_at') or datetime.now(timezone.utc).isoformat()
        if IS_PG:
            q("""INSERT INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)
                 ON CONFLICT (nombre) DO UPDATE SET datos = EXCLUDED.datos, actualizado = EXCLUDED.actualizado""", (n, d, act))
        else:
            q('INSERT OR REPLACE INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)', (n, d, act))
        guardados[n] = n_new
    _live_cache['t'] = 0.0
    return jsonify({'ok': True, 'guardados': guardados, 'estado': _sync_resumen()})


@app.get('/admin/portal-sync')
def portal_sync_estado():
    if not _sync_key_ok():
        return jsonify({'error': 'forbidden'}), 403
    _live_cache['t'] = 0.0
    return jsonify(_sync_resumen())


@app.post('/admin/crm-write')
def crm_write_api():
    """Escrituras al CRM propio. Misma llave que el portal-sync. No imprime cargas."""
    if not os.environ.get('PORTAL_SYNC_KEY'):
        return jsonify({'error': 'sync no configurado'}), 503
    if not _sync_key_ok():
        return jsonify({'error': 'forbidden'}), 403
    body = request.get_json(silent=True) or {}
    op = body.get('op') or ''
    data = body.get('data') if isinstance(body.get('data'), dict) else {k: v for k, v in body.items() if k != 'op'}
    try:
        import crm_write
        out = crm_write.apply(q, op, data)
        _live_cache['t'] = 0.0
        return jsonify(out), (200 if out.get('ok') else 400)
    except Exception:
        log.exception('crm-write op=%s', op)
        return jsonify({'ok': False, 'error': 'crm-write falló'}), 500


# ---------------------------------------------------------------- lecturas del CRM propio (reemplazo de GET a GHL)
# Misma llave que /admin/portal-sync y /admin/crm-write (PORTAL_SYNC_KEY). Solo lectura.
def _crm_read_guard():
    if not os.environ.get('PORTAL_SYNC_KEY'):
        return jsonify({'error': 'sync no configurado'}), 503
    if not _sync_key_ok():
        return jsonify({'error': 'forbidden'}), 403
    if not IS_PG:
        return jsonify({'error': 'solo Postgres'}), 503
    return None


def _arg_fecha(nombre):
    v = (request.args.get(nombre) or '').strip()
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace('Z', '+00:00')).isoformat()
    except ValueError:
        abort(400)


@app.get('/admin/crm-read/contactos')
def crm_read_contactos():
    err = _crm_read_guard()
    if err:
        return err
    import crm_read
    try:
        lim = int(request.args.get('limite') or crm_read.LIMITE)
    except ValueError:
        abort(400)
    rows = crm_read.contactos(q, _arg_fecha('desde'), _arg_fecha('hasta'), _arg_fecha('actualizados_desde'), lim)
    return jsonify({'contacts': rows, 'n': len(rows), 'fuente': 'crm'})


@app.get('/admin/crm-read/contacto/<cid>')
def crm_read_contacto(cid):
    err = _crm_read_guard()
    if err:
        return err
    import crm_read
    c = crm_read.contacto(q, cid)
    return (jsonify({'contact': c}), 200) if c else (jsonify({'error': 'no encontrado'}), 404)


@app.get('/admin/crm-read/asignaciones')
def crm_read_asignaciones():
    err = _crm_read_guard()
    if err:
        return err
    import crm_read
    desde = _arg_fecha('desde')
    if not desde:
        abort(400)
    rows = crm_read.asignaciones(q, desde, _arg_fecha('hasta'))
    return jsonify({'asignaciones': rows, 'n': len(rows)})


@app.get('/admin/crm-read/wa-nuevos')
def crm_read_wa_nuevos():
    err = _crm_read_guard()
    if err:
        return err
    import crm_read
    desde, hasta = _arg_fecha('desde'), _arg_fecha('hasta')
    if not (desde and hasta):
        abort(400)
    rows = crm_read.wa_nuevos(q, desde, hasta)
    return jsonify({'nuevos': rows, 'n': len(rows)})


# ---------------------------------------------------------------- enlaces de portales desde Inventario
# Llave propia de Inventario (INVENTARIO_LINKS_KEY_SHA256; en Render solo el SHA-256). Solo sirve para
# estas dos rutas: no da acceso al portal, al CRM ni a Mensajes. Sin la variable, las rutas quedan cerradas.
def _links_key_ok():
    given = request.headers.get('X-Inventario-Links-Key', '')
    if _secret_matches(given, 'INVENTARIO_LINKS_KEY', 'INVENTARIO_LINKS_KEY_SHA256'):
        return True
    return bool(os.environ.get('PORTAL_SYNC_KEY')) and _sync_key_ok()  # llave de la caja (superconjunto)


def _links_guard():
    if not (os.environ.get('INVENTARIO_LINKS_KEY') or os.environ.get('INVENTARIO_LINKS_KEY_SHA256')
            or os.environ.get('PORTAL_SYNC_KEY')):
        return jsonify({'error': 'importación de enlaces no configurada'}), 503
    if not _links_key_ok():
        return jsonify({'error': 'forbidden'}), 403
    return None


@app.post('/admin/portal-links/import')
def portal_links_import_api():
    """Cuerpo JSON: {"texto": "código | portal | url ...", "dry_run": true, "verificar": true}.
    Por defecto es simulacro (dry_run=true). Nunca borra; reemplazos quedan en crm_portal_links_log."""
    err = _links_guard()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    texto = body.get('texto')
    if not isinstance(texto, str) or not texto.strip() or len(texto) > 200_000:
        return jsonify({'error': 'texto vacío o demasiado grande'}), 400
    import links_inventario
    _, items, _ = _mercadeo_items({'role': 'admin', 'user': 'inventario'})
    if not items:
        return jsonify({'error': 'inventario vacío; no se importa'}), 409
    fuente = str(body.get('fuente') or 'inventario: lista de Inventario')[:120]
    res = links_inventario.importar(q, items, texto, dry_run=body.get('dry_run', True) is not False,
                                    verificar=body.get('verificar', True) is not False, fuente=fuente)
    return jsonify(res)


@app.get('/admin/portal-links/export')
def portal_links_export_api():
    err = _links_guard()
    if err:
        return err
    import links_inventario
    return jsonify(links_inventario.exportar(q))


def _legal_page(page, title):
    return _panel_headers(render_template('legal.html', page=page, title=title))


@app.get('/privacidad')
def privacidad():
    return _legal_page('privacidad', 'Política de privacidad')


@app.get('/eliminar-datos')
def eliminar_datos():
    return _legal_page('eliminar', 'Eliminar datos')


@app.get('/terminos')
def terminos():
    return _legal_page('terminos', 'Términos de servicio')


@app.route('/login', methods=['GET', 'POST'])
def login():
    user = _current_user()
    next_url = _safe_next(request.values.get('next'))
    if user and request.method == 'GET':
        return redirect(next_url)
    error = ''
    usuario = (request.form.get('usuario') or '').strip()
    if request.method == 'POST':
        if _too_many_login_fails(usuario):
            error = 'Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo.'
            log.info('login rate-limited ip=%s user=%s', _client_ip(), usuario.lower())
        else:
            found = portal_lib.identify_user(usuario, request.form.get('password') or '')
            if not found:
                _record_login_fail(usuario)
                error = 'Usuario o contraseña incorrectos.'
                log.info('login failed ip=%s user=%s', _client_ip(), usuario.lower())
            else:
                _clear_login_fails(usuario)
                session.clear()
                session.permanent = True
                session['uid'] = found['user']
                log.info('login ok user=%s', found['user'])
                return redirect(next_url)
    return _panel_headers(render_template(
        'login.html', error=error, usuario=usuario, next_url=next_url if next_url != '/portal' else ''))


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    resp = _panel_headers(redirect('/login'))
    resp.set_cookie(
        app.config['SESSION_COOKIE_NAME'], '', expires=0, max_age=0,
        httponly=True, secure=bool(app.config.get('SESSION_COOKIE_SECURE')),
        samesite='Lax', path='/',
    )
    return resp


@app.get('/')
@app.get('/portal')
@require_login
def portal():
    if g.user['role'] != 'admin':
        return _render_asesor(g.user)
    return _panel_headers(render_template('portal.html', dash=_dashboard(g.user), fases=_fases_de(_clientes_dashboard(g.user)), **_portal_links()))


@app.get('/clientes-propios')
@require_login
def clientes_propios():
    if g.user['role'] not in ('admin', 'lider'):
        return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
    slugs = portal_lib.visible_slugs(g.user)
    filas = []
    for r in _db_propios(slugs):
        filas.append({
            'asesor_nombre': _nombre_asesor(r['asesor_slug']),
            'nombre': r['nombre'], 'telefono': r['telefono'],
            'busca': _busca(r), 'origen_nota': r.get('origen_nota') or '',
            'status': r['status'],
        })
    return _panel_headers(render_template('propios.html', filas=filas))


@app.get('/mis-clientes')
@require_login
def mis_clientes():
    ok = request.args.get('ok')
    flash_ok = 'Cliente guardado.' if ok == '1' else ('Estatus actualizado.' if ok == 'estatus' else '')
    return _render_clientes(g.user, flash_ok=flash_ok)


def _equipo_de(user):
    """Asesoras que este usuario puede supervisar (servidor). Admin: todas; líder: su equipo."""
    if user['role'] == 'admin':
        return [a for a in portal_lib.asesores_meta()]
    if user['role'] == 'lider':
        permitidos = set(portal_lib.visible_slugs(user)) - {user['user']}
        return [a for a in portal_lib.asesores_meta() if a['user'] in permitidos]
    return None


@app.get('/mi-equipo')
@require_login
def mi_equipo():
    equipo = _equipo_de(g.user)
    if equipo is None:
        return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
    asig_all, _, _ = portal_lib.load_asignados(slugs=[a['user'] for a in equipo]) if equipo else ([], '', '')
    cap_all = portal_lib.load_captaciones(slugs=[a['user'] for a in equipo]) if equipo else []
    filas = []
    for a in equipo:
        s = a['user']
        filas.append({
            'user': s, 'name': a.get('name') or s, 'role': a.get('role'),
            'lider': _nombre_asesor(a.get('lider')) if a.get('lider') and a.get('lider') != s else '',
            'n_prop': sum(1 for x in cap_all if x.get('asesor') == s and x.get('origen') == 'propio'),
            'n_ali': sum(1 for x in cap_all if x.get('asesor') == s and x.get('origen') == 'alianza'),
            'n_cli': sum(1 for x in asig_all if x.get('asesor') == s) + len(_db_propios([s])),
        })
    return _panel_headers(render_template('equipo.html', filas=filas))


@app.get('/mi-equipo/<slug>')
@require_login
def mi_equipo_asesora(slug):
    equipo = _equipo_de(g.user)
    slug = (slug or '').strip().lower()
    if equipo is None or slug not in {a['user'] for a in equipo}:
        return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))
    meta = portal_lib.asesor_by_user(slug) or {}
    cap = portal_lib.load_captaciones(slug=slug)
    clientes, cli_fecha = _asesor_clientes({'user': slug})
    for c in clientes:
        c['editable'] = False
    inv = _inventario()
    visitas_r = q('SELECT * FROM asesor_visitas WHERE asesor_slug=%s ORDER BY fecha DESC, id DESC', (slug,), fetch=True) or []
    ofertas_r = q('SELECT * FROM asesor_ofertas WHERE asesor_slug=%s ORDER BY fecha DESC, id DESC', (slug,), fetch=True) or []
    cli_est, _ = _clientes_estatus(slug)
    return _panel_headers(render_template(
        'equipo_asesora.html', a_name=meta.get('name') or slug, a_slug=slug, pf=_perfil(slug),
        visitas=visitas_r, visitas_estatus=[c for c in cli_est if c['estatus'] == 'Visita agendada'], ofertas=ofertas_r,
        propias=_fichas_publicas([x for x in cap if x.get('origen') == 'propio']),
        alianzas=_fichas_publicas([x for x in cap if x.get('origen') == 'alianza']),
        clientes=clientes, fases=_fases_de(clientes),
        cli_fecha=portal_lib.fmt_fecha(cli_fecha) if cli_fecha else 'fecha no disponible',
        inv_fecha=_fmt_actualizado(inv.get('actualizado') or ''),
    ))


@app.post('/asesor/clientes')
@require_login
def asesor_nuevo_cliente():
    if g.user['role'] == 'admin':
        return _panel_headers(Response('Los clientes propios los registra cada asesor en su portal.\n', 403))
    if not portal_lib.csrf_ok(g.user, request.form.get('csrf')):
        return _render_clientes(g.user, flash_err='La sesión expiró. Vuelve a intentar.')
    datos, errores = portal_lib.validar_cliente(request.form)
    if errores:
        return _render_clientes(g.user, flash_err=' '.join(errores), form=datos)
    now = now_iso()
    try:
        q(
            "INSERT INTO asesor_clientes "
            "(asesor_slug, nombre, telefono, operacion, tipo, zona, presupuesto, origen_nota, status, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (g.user['user'], datos['nombre'], datos['telefono'], datos['operacion'], datos['tipo'],
             datos['zona'], datos['presupuesto'], datos['origen_nota'], datos['status'], now, now),
        )
    except Exception as e:
        msg = str(e).lower()
        if 'unique' in msg or 'duplicate' in msg:
            return _render_clientes(g.user, flash_err='Ya tienes un cliente con ese teléfono.', form=datos)
        log.error('No se pudo guardar cliente propio: %s', type(e).__name__)
        return _render_clientes(g.user, flash_err='No se pudo guardar. Inténtalo de nuevo.', form=datos)
    try:
        rows = q('SELECT id FROM asesor_clientes WHERE asesor_slug=%s AND telefono=%s ORDER BY id DESC LIMIT 1',
                 (g.user['user'], datos['telefono']), fetch=True) or []
        import crm_write
        crm_write.upsert_propio(q, {
            'propio_id': rows[0]['id'] if rows else None,
            'name': datos['nombre'], 'phone': datos['telefono'], 'asesor_slug': g.user['user'],
            'operacion': datos['operacion'], 'estatus': datos['status'], 'estado': datos['status'],
            'origen': 'Propio', 'source_kind': 'asesor',
        })
        _live_cache['t'] = 0.0
    except Exception:
        log.exception('crm propio')
    return redirect('/mis-clientes?ok=1')


@app.post('/asesor/clientes/<int:cid>/estatus')
@require_login
def asesor_estatus_cliente(cid):
    if not portal_lib.csrf_ok(g.user, request.form.get('csrf')):
        return _render_clientes(g.user, flash_err='La sesión expiró. Vuelve a intentar.')
    status = (request.form.get('status') or '').strip()
    if status not in portal_lib.ESTATUS_OPCIONES:
        return _render_clientes(g.user, flash_err='El estatus no es válido.')
    rows = q('SELECT id, asesor_slug FROM asesor_clientes WHERE id=%s', (cid,), fetch=True) or []
    if not rows:
        return _render_clientes(g.user, flash_err='No encontramos ese cliente.')
    if not portal_lib.can_edit_propio(g.user, rows[0]['asesor_slug']):
        return _panel_headers(Response('No puedes editar este cliente.\n', 403))
    q('UPDATE asesor_clientes SET status=%s, updated_at=%s WHERE id=%s', (status, now_iso(), cid))
    try:
        import crm_write
        crm_write.update_propio_status(q, cid, status)
        _live_cache['t'] = 0.0
    except Exception:
        log.exception('crm propio status')
    return redirect('/mis-clientes?ok=estatus')



@app.get('/inventario')
@require_login
def inventario():
    """Inventario General: solo lectura para todos los roles (autorizado por José)."""
    data = _inventario()
    grupos = data.get('grupos') or {}
    prop, ali, red = grupos.get('propio') or [], grupos.get('alianza') or [], grupos.get('red') or []
    f = request.args.get('f') or 'propias'
    if f not in ('propias', 'aliados', 'todos'):
        f = 'propias'
    filas = prop if f == 'propias' else ali if f == 'aliados' else (prop + ali)
    return _panel_headers(render_template(
        'inventario.html',
        actualizado=_fmt_actualizado(data.get('actualizado') or ''),
        filas=_fichas_publicas(filas, data), f=f,
        n_propio=len(prop), n_alianza=len(ali), n_red=len(red), n_todos=len(prop) + len(ali),
    ))



# ---------------------------------------------------------------- Mercadeo: 10 portales por inmueble
def _mercadeo_items(user):
    """Inmuebles que este usuario puede ver en Mercadeo (filtro en servidor).

    Admin (Presidencia) y Mercadeo: todo el inventario propio y en alianza de la inmobiliaria.
    Líder: sus captaciones y las de su equipo (mismo alcance que Mi Equipo).
    Asesora: solo sus captaciones (propias y en alianza), igual que Mis Captaciones.
    """
    inv = _inventario()
    grupos = inv.get('grupos') or {}
    cap_all = portal_lib.load_captaciones()
    asesor_de = {}
    for c in cap_all:
        asesor_de.setdefault(portales.key_de(c), c.get('asesor') or '')
    if user['role'] in ROLES_MERCADEO:
        items = list(grupos.get('propio') or []) + list(grupos.get('alianza') or [])
    else:
        slugs = set(portal_lib.visible_slugs(user))
        items = [c for c in cap_all if c.get('asesor') in slugs]
    return inv, items, asesor_de


def _mercadeo_render(user, *, ok='', err='', status=200):
    inv, items, asesor_de = _mercadeo_items(user)
    es_admin = user['role'] in ROLES_MERCADEO   # Presidencia y Mercadeo administran enlaces y verificación
    f = request.args.get('f') or 'todos'
    if f not in ('propias', 'aliados', 'todos'):
        f = 'todos'
    try:
        links = portales.links_por_key(q)
    except Exception:
        log.exception('mercadeo links')
        links = {}
    try:
        mp_links = marketplace.links_por_key(q)
    except Exception:
        log.exception('marketplace links')
        mp_links = {}
    puede_mp = user['role'] == 'admin'   # José y Luz (rol admin) editan Marketplace
    fichas = _fichas_publicas(items, inv)
    filas, n_prop, n_ali = [], 0, 0
    conteo = {p['key']: 0 for p in portales.PORTALES}
    for it, ficha in zip(items, fichas):
        origen = it.get('origen') or ''
        n_prop += origen == 'propio'
        n_ali += origen == 'alianza'
        if f == 'propias' and origen != 'propio' or f == 'aliados' and origen != 'alianza':
            continue
        key = portales.key_de(it)
        cas = portales.casillas(links.get(key))
        for c in cas:
            conteo[c['key']] += c['visible']
        slug = it.get('asesor') or asesor_de.get(key) or ''
        filas.append({
            **ficha, 'key': key, 'origen': origen, 'casillas': cas,
            'n_pub': sum(1 for c in cas if c['visible']),
            'asesor': _nombre_asesor(slug) if slug else '',
            'mp': mp_links.get(key),
            'mp_hist': marketplace.historial(q, key) if puede_mp and mp_links.get(key) else [],
        })
    resumen = [{**p, 'n': conteo[p['key']]} for p in portales.PORTALES]
    return _hdr(render_template(
        'mercadeo.html', filas=filas, f=f, n_prop=n_prop, n_ali=n_ali, n_todos=n_prop + n_ali,
        resumen=resumen, portales_lista=portales.PORTALES, es_admin=es_admin,
        ver_asesor=user['role'] in ('admin', 'lider', 'mercadeo'),
        redes=redes.redes(), mp_logo=marketplace.LOGO, puede_mp=puede_mp,
        n_mp=sum(1 for k in mp_links if k in {portales.key_de(x) for x in items}),
        job=portales.estado_job() if es_admin else None,
        inv_fecha=_fmt_actualizado(inv.get('actualizado') or ''),
        csrf=portal_lib.csrf_token(user) if (es_admin or puede_mp) else '', ok=ok, err=err,
        fmt=_fmt_actualizado,
    ), status)


@app.get('/mercadeo')
@require_login
def mercadeo():
    ok = {'1': 'Enlace guardado y verificado.', 'quitado': 'Enlace quitado.',
          'verificando': 'Verificando todos los enlaces en segundo plano. Recarga en un minuto.',
          'mp': 'Enlace de Marketplace guardado.', 'mp_quitado': 'Enlace de Marketplace quitado.',
          'mp_igual': 'Ese enlace de Marketplace ya estaba guardado.'}.get(request.args.get('ok') or '', '')
    return _mercadeo_render(g.user, ok=ok)


@app.post('/mercadeo/link')
@require_roles(*ROLES_MERCADEO)
def mercadeo_link():
    """Solo Presidencia y Mercadeo agregan, cambian o quitan enlaces. Se verifica antes de guardar."""
    if not _csrf_ok():
        return _mercadeo_render(g.user, err='La sesión expiró. Vuelve a intentar.', status=400)
    f = request.form
    key, portal, accion = (f.get('key') or '').strip(), (f.get('portal') or '').strip(), f.get('accion') or 'guardar'
    if portal not in portales.PORTAL_KEYS:
        return _mercadeo_render(g.user, err='Portal no válido.', status=400)
    _, items, _ = _mercadeo_items(g.user)
    it = next((x for x in items if portales.key_de(x) == key), None)
    if not it:
        return _mercadeo_render(g.user, err='No encontramos ese inmueble en el inventario.', status=400)
    nombre = portales.POR_KEY[portal]['nombre']
    if accion == 'quitar':
        portales.quitar(q, key=key, portal=portal, por=g.user['user'])
        return redirect('/mercadeo?ok=quitado')
    url, e = portales.url_valida(portal, f.get('url'))
    if e:
        return _mercadeo_render(g.user, err=e, status=400)
    estado, http_status, detalle = portales.verificar(portal, url, timeout=8)
    if estado == 'caido':
        return _mercadeo_render(g.user, err=f'No se guardó: el enlace de {nombre} no responde ({detalle or "sin respuesta"}). '
                                             'Revisa que sea la ficha publicada del inmueble.', status=400)
    portales.guardar(q, it=it, portal=portal, url=url, estado=estado, http_status=http_status,
                     fuente='manual (Mercadeo)' if g.user['role'] == 'mercadeo' else 'manual (Presidencia)',
                     nota=detalle or None, por=g.user['user'])
    return redirect('/mercadeo?ok=1')


@app.post('/mercadeo/verificar')
@require_roles(*ROLES_MERCADEO)
def mercadeo_verificar():
    if not _csrf_ok():
        return _mercadeo_render(g.user, err='La sesión expiró. Vuelve a intentar.', status=400)
    portales.reverificar_todo(q)
    return redirect('/mercadeo?ok=verificando')


@app.post('/mercadeo/marketplace')
@require_admin
def mercadeo_marketplace():
    """Facebook Marketplace por inmueble: solo rol admin (José y Luz). Valida URL y deja bitácora (quién/cuándo)."""
    if not _csrf_ok():
        return _mercadeo_render(g.user, err='La sesión expiró. Vuelve a intentar.', status=400)
    f = request.form
    key, accion = (f.get('key') or '').strip(), f.get('accion') or 'guardar'
    _, items, _ = _mercadeo_items(g.user)
    it = next((x for x in items if portales.key_de(x) == key), None)
    if not it:
        return _mercadeo_render(g.user, err='No encontramos ese inmueble en el inventario.', status=400)
    if accion == 'quitar':
        marketplace.quitar(q, key=key, por=g.user['user'])
        return redirect('/mercadeo?ok=mp_quitado')
    url, e = marketplace.url_valida(f.get('url'))
    if e:
        return _mercadeo_render(g.user, err=e, status=400)
    res = marketplace.guardar(q, key=key, codigo=portales.codigo_de(it), url=url, por=g.user['user'], is_pg=IS_PG)
    return redirect('/mercadeo?ok=' + ('mp_igual' if res == 'sin_cambio' else 'mp'))


@app.get('/mercadeo/planificador')
@require_roles(*ROLES_MERCADEO)
def mercadeo_planificador():
    """Planificador de anuncios (Mercadeo y Presidencia). Solo lectura por ahora: muestra las publicaciones
    programadas que la caja sube con /admin/portal-sync («planificador.json»). No publica nada."""
    d = _portal_live('planificador.json') or {}
    posts = []
    for p in d.get('posts') or []:
        if not isinstance(p, dict):
            continue
        posts.append({
            'cuando': p.get('fecha_hora_toronto') or '',
            'fecha': (p.get('fecha_hora_toronto') or '')[:10],
            'cuentas': [c.strip() for c in str(p.get('cuentas') or '').split('|') if c.strip()],
            'texto': p.get('texto') or '',
            'media': [m.strip() for m in str(p.get('media_urls') or '').split('|') if m.strip().startswith('https://')],
            'id': p.get('post_id') or '',
        })
    posts.sort(key=lambda x: x['cuando'])
    return _hdr(render_template('planificador.html', posts=posts, fuente=d.get('fuente') or '',
                                actualizado=_fmt_actualizado(d.get('actualizado') or '') if d else ''))


# ---------------------------------------------------------------- portal de asesoras (fase 2)
FOTO_MAX = 2 * 1024 * 1024
FOTO_TIPOS = {b'\xff\xd8\xff': 'image/jpeg', b'\x89PNG\r\n\x1a\n': 'image/png'}


def _foto_mime(data):
    for sig, mime in FOTO_TIPOS.items():
        if data.startswith(sig):
            return mime
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _txt(form, k, n):
    import re as _re
    return _re.sub(r'\s+', ' ', (form.get(k) or '').strip())[:n]


def _perfil(slug):
    rows = q('SELECT asesor_slug, telefono, email, cedula, bio, zonas, foto_mime, updated_at FROM asesor_perfil WHERE asesor_slug=%s',
             (slug,), fetch=True) or []
    return rows[0] if rows else {}


def _email_de(slug):
    return (_perfil(slug).get('email') or '').strip()


def _correo(slug, asunto, cuerpo, evento, **kw):
    return notif.queue(q, now_iso, to_slug=slug, to_addr=_email_de(slug), subject=asunto, body=cuerpo, evento=evento, **kw)


def _supervisa(user, slug):
    """Admin: todas; líder: su equipo (sin sí misma); asesora: nadie."""
    if user['role'] == 'admin':
        return bool(portal_lib.asesor_by_user(slug))
    if user['role'] == 'lider':
        return slug != user['user'] and slug in portal_lib.visible_slugs(user)
    return False


def _aprobador_de(slug):
    meta = portal_lib.asesor_by_user(slug) or {}
    lider = meta.get('lider') or ''
    return lider if lider and lider != slug else 'jose'


def _csrf_ok():
    return portal_lib.csrf_ok(g.user, request.form.get('csrf'))


def _hdr(html, status=200):
    r = _panel_headers(html)
    r.status_code = status
    return r


def _forbidden():
    return _panel_headers(Response('No tienes acceso a esta sección.\n', 403))


# --- Mi Perfil
@app.route('/perfil', methods=['GET', 'POST'])
@require_login
def perfil():
    u = g.user
    if u['role'] == 'admin':
        filas = []
        for a in portal_lib.asesores_meta():
            pf = _perfil(a['user'])
            filas.append({'user': a['user'], 'name': a.get('name'), 'email': pf.get('email') or '', 'telefono': pf.get('telefono') or '',
                          'zonas': pf.get('zonas') or '', 'foto': bool(pf.get('foto_mime')), 'updated': _fecha_corta(pf.get('updated_at') or '') if pf else ''})
        return _hdr(render_template('perfiles_admin.html', filas=filas))
    ok, err = '', ''
    if request.method == 'POST':
        if not _csrf_ok():
            err = 'La sesión expiró. Vuelve a intentar.'
        elif (request.content_length or 0) > FOTO_MAX + 64 * 1024:
            err = 'La foto supera 2 MB.'
        else:
            f = request.form
            email = _txt(f, 'email', 120)
            if email and ('@' not in email or ' ' in email):
                err = 'El correo no es válido.'
            tel_raw = _txt(f, 'telefono', 40)
            tel, tel_err = portal_lib.normalizar_telefono(tel_raw) if tel_raw else ('', None)
            if tel_err:
                err = tel_err
            foto = request.files.get('foto')
            data = mime = None
            if foto and foto.filename:
                data = foto.read(FOTO_MAX + 1)
                if len(data) > FOTO_MAX:
                    err = 'La foto supera 2 MB.'
                else:
                    mime = _foto_mime(data)
                    if not mime:
                        err = 'La foto debe ser JPG, PNG o WebP.'
            if not err:
                prev = _perfil(u['user'])
                vals = (tel or '', email, _txt(f, 'cedula', 20), (f.get('bio') or '').strip()[:600], _txt(f, 'zonas', 200), now_iso())
                if prev:
                    q('UPDATE asesor_perfil SET telefono=%s, email=%s, cedula=%s, bio=%s, zonas=%s, updated_at=%s WHERE asesor_slug=%s',
                      vals + (u['user'],))
                else:
                    q('INSERT INTO asesor_perfil (telefono, email, cedula, bio, zonas, updated_at, asesor_slug) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                      vals + (u['user'],))
                if data:
                    q('UPDATE asesor_perfil SET foto=%s, foto_mime=%s WHERE asesor_slug=%s', (data, mime, u['user']))
                _correo(u['user'], 'Tu perfil fue actualizado',
                        f"Hola {u['name'].split(' ')[0]},\n\nTu perfil en For Rent Online fue actualizado el {_fmt_actualizado(now_iso())}.\n"
                        "Si no fuiste tú, avísale a José.\n\nFor Rent Online", 'perfil_actualizado')
                return redirect('/perfil?ok=1')
    if request.args.get('ok') == '1':
        ok = 'Perfil guardado. Te enviamos un aviso por correo.'
    return _hdr(render_template('perfil.html', pf=_perfil(u['user']), ok=ok, err=err, csrf=portal_lib.csrf_token(u)), 400 if err else 200)


@app.get('/perfil/foto/<slug>')
@require_login
def perfil_foto(slug):
    slug = (slug or '').lower()
    if not (g.user['user'] == slug or _supervisa(g.user, slug)):
        return _forbidden()
    rows = q('SELECT foto, foto_mime FROM asesor_perfil WHERE asesor_slug=%s', (slug,), fetch=True) or []
    if not rows or not rows[0].get('foto'):
        abort(404)
    r = make_response(bytes(rows[0]['foto']))
    r.headers['Content-Type'] = rows[0]['foto_mime'] or 'application/octet-stream'
    r.headers['Cache-Control'] = 'private, max-age=300'
    r.headers['X-Content-Type-Options'] = 'nosniff'
    return r


# --- Mis Captaciones (filtros) + solicitudes de alta/baja
@app.route('/mis-captaciones', methods=['GET', 'POST'], endpoint='mis_captaciones2')
@require_login
def mis_captaciones2():
    u = g.user
    err, ok = '', ''
    if request.method == 'POST':
        if u['role'] == 'admin':
            return _forbidden()
        if not _csrf_ok():
            err = 'La sesión expiró. Vuelve a intentar.'
        else:
            f = request.form
            accion = f.get('accion')
            if accion not in ('alta', 'baja'):
                err = 'Acción no válida.'
            else:
                mias = {x.get('codigo') for x in portal_lib.load_captaciones(slug=u['user'])}
                codigo = _txt(f, 'codigo', 40)
                titulo = _txt(f, 'titulo', 160)
                origen = f.get('origen') if f.get('origen') in ('propio', 'alianza') else ''
                if accion == 'baja' and codigo not in mias:
                    err = 'Solo puedes pedir el retiro de tus propias captaciones.'
                elif accion == 'alta' and len(titulo) < 4:
                    err = 'Indica el título o la dirección del inmueble.'
                if not err:
                    q('INSERT INTO captacion_solicitudes (asesor_slug, accion, codigo, titulo, operacion, tipo, zona, precio, origen, nota, estado, created_at) '
                      'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                      (u['user'], accion, codigo, titulo, _txt(f, 'operacion', 20), _txt(f, 'tipo', 60), _txt(f, 'zona', 120),
                       _txt(f, 'precio', 60), origen, (f.get('nota') or '').strip()[:400], 'pendiente', now_iso()))
                    que = 'agregar' if accion == 'alta' else 'retirar'
                    det = f"{titulo or codigo}" + (f" (código {codigo})" if codigo else '')
                    _correo(u['user'], f"Solicitud para {que} una captación",
                            f"Hola {u['name'].split(' ')[0]},\n\nRegistramos tu solicitud para {que} la captación: {det}.\n"
                            "La revisará tu líder; el inventario no cambia hasta que se apruebe.\n\nFor Rent Online", f'captacion_{accion}')
                    apr = _aprobador_de(u['user'])
                    aviso = f"Asesora: {u['name']}\nAcción: {que}\nInmueble: {det}\n\nApruébala o recházala en For Rent Online › Aprobaciones.\n"
                    if apr == 'jose':
                        notif.queue(q, now_iso, to_slug='jose', to_addr=notif.ADMIN_NOTIFY,
                                    subject=f"{u['name']} pidió {que} una captación", body=aviso, evento=f'captacion_{accion}_aprobador')
                    else:
                        _correo(apr, f"{u['name']} pidió {que} una captación", aviso, f'captacion_{accion}_aprobador')
                    return redirect('/mis-captaciones?ok=1')
    f = request.args.get('f') or 'todas'
    if f not in ('propias', 'aliadas', 'todas'):
        f = 'todas'
    cap = portal_lib.load_captaciones(slug=u['user'])
    prop = [x for x in cap if x.get('origen') == 'propio']
    ali = [x for x in cap if x.get('origen') == 'alianza']
    filas = prop if f == 'propias' else ali if f == 'aliadas' else prop + ali
    sol = q('SELECT * FROM captacion_solicitudes WHERE asesor_slug=%s ORDER BY id DESC', (u['user'],), fetch=True) or []
    if request.args.get('ok') == '1':
        ok = 'Solicitud registrada. Te enviamos un aviso por correo.'
    inv = _inventario()
    return _hdr(render_template('captaciones.html', filas=_fichas_publicas(filas, inv), f=f, n_prop=len(prop), n_ali=len(ali), solicitudes=sol,
                                inv_fecha=_fmt_actualizado(inv.get('actualizado') or ''), ok=ok, err=err,
                                csrf=portal_lib.csrf_token(u), puede=u['role'] != 'admin'), 400 if err else 200)


# --- Aprobaciones de captaciones (líder de la asesora; José todas)
@app.get('/aprobaciones')
@require_login
def aprobaciones():
    u = g.user
    if u['role'] not in ('admin', 'lider'):
        return _forbidden()
    rows = q('SELECT * FROM captacion_solicitudes ORDER BY id DESC', fetch=True) or []
    rows = [r for r in rows if _supervisa(u, r['asesor_slug'])]
    for r in rows:
        r['asesor_nombre'] = _nombre_asesor(r['asesor_slug'])
    t = 'historial' if request.args.get('t') == 'historial' else 'pendientes'
    pend = [r for r in rows if r['estado'] == 'pendiente']
    hist = [r for r in rows if r['estado'] != 'pendiente']
    ok = {'aprobada': 'Solicitud aprobada. Avisamos a la asesora e Inventario.', 'rechazada': 'Solicitud rechazada. Avisamos a la asesora.'}.get(request.args.get('ok'), '')
    return _hdr(render_template('aprobaciones.html', filas=pend if t == 'pendientes' else hist, t=t, n_pend=len(pend), n_hist=len(hist),
                                ok=ok, err='', csrf=portal_lib.csrf_token(u)))


@app.post('/aprobaciones/<int:sid>')
@require_login
def aprobar_solicitud(sid):
    u = g.user
    if u['role'] not in ('admin', 'lider'):
        return _forbidden()
    if not _csrf_ok():
        abort(400)
    rows = q('SELECT * FROM captacion_solicitudes WHERE id=%s', (sid,), fetch=True) or []
    if not rows:
        abort(404)
    r = rows[0]
    if not _supervisa(u, r['asesor_slug']):
        return _forbidden()
    if r['estado'] != 'pendiente':
        return redirect('/aprobaciones?t=historial')
    dec = request.form.get('decision')
    if dec not in ('aprobada', 'rechazada'):
        abort(400)
    nota = (request.form.get('nota') or '').strip()[:400]
    q('UPDATE captacion_solicitudes SET estado=%s, revisado_por=%s, revisado_at=%s, nota_revision=%s WHERE id=%s AND estado=%s',
      (dec, u['user'], now_iso(), nota, sid, 'pendiente'))
    que = 'agregar' if r['accion'] == 'alta' else 'retirar'
    det = (r['titulo'] or r['codigo'] or 'inmueble') + (f" (código {r['codigo']})" if r['codigo'] else '')
    nombre = _nombre_asesor(r['asesor_slug']).split(' ')[0]
    cuerpo = (f"Hola {nombre},\n\nTu solicitud para {que} la captación {det} fue {dec} por {u['name']}.\n"
              + (f"Nota: {nota}\n" if nota else '')
              + ("Queda pendiente de cargar en el inventario.\n" if dec == 'aprobada' else '') + "\nFor Rent Online")
    _correo(r['asesor_slug'], f"Solicitud de captación {dec}", cuerpo, f'captacion_{dec}')
    if dec == 'aprobada':
        notif.queue(q, now_iso, to_slug='inventario', to_addr=notif.ADMIN_NOTIFY,
                    subject=f"Cargar en inventario: {que} captación de {_nombre_asesor(r['asesor_slug'])}",
                    body=(f"Aprobada por {u['name']}.\nAcción: {que}\nInmueble: {det}\nOperación: {r['operacion'] or '—'} · Tipo: {r['tipo'] or '—'}"
                          f" · Zona: {r['zona'] or '—'} · Precio: {r['precio'] or '—'} · Origen: {r['origen'] or '—'}\nNota de la asesora: {r['nota'] or '—'}\n"),
                    evento='captacion_cargar_inventario')
    return redirect(f'/aprobaciones?ok={dec}')


# --- Visitas
def _clientes_estatus(slug):
    asig, act, _ = portal_lib.load_asignados(slug=slug)
    out = [{'nombre': c.get('nombre') or 'Cliente', 'estatus': c.get('estatus') or '', 'inmueble': c.get('inmueble') or '', 'origen': 'Asignado'} for c in asig]
    out += [{'nombre': r['nombre'], 'estatus': r['status'], 'inmueble': _busca(r), 'origen': 'Propio'} for r in _db_propios([slug])]
    return out, act


@app.route('/visitas', methods=['GET', 'POST'])
@require_login
def visitas():
    u = g.user
    err = ''
    if request.method == 'POST':
        if u['role'] == 'admin':
            return _forbidden()
        f = request.form
        cliente = _txt(f, 'cliente', 120)
        fecha = _txt(f, 'fecha', 10)
        hora = _txt(f, 'hora', 5)
        if not _csrf_ok():
            err = 'La sesión expiró. Vuelve a intentar.'
        elif len(cliente) < 2:
            err = 'Indica el cliente.'
        elif fecha and not _fecha_valida(fecha):
            err = 'La fecha no es válida.'
        else:
            q('INSERT INTO asesor_visitas (asesor_slug, fecha, hora, cliente, inmueble, nota, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
              (u['user'], fecha, hora, cliente, _txt(f, 'inmueble', 160), (f.get('nota') or '').strip()[:400], now_iso()))
            try:
                import crm_write
                crm_write.insert_visit(q, {
                    'asesor_slug': u['user'], 'fecha': fecha, 'hora': hora, 'cliente': cliente,
                    'inmueble': _txt(f, 'inmueble', 160), 'nota': (f.get('nota') or '').strip()[:400],
                    'source': 'portal',
                })
                _live_cache['t'] = 0.0
            except Exception:
                log.exception('crm visita')
            return redirect('/visitas?ok=1')
    cli, act = _clientes_estatus(u['user'])
    por_estatus = [c for c in cli if c['estatus'] == 'Visita agendada']
    propias = q('SELECT * FROM asesor_visitas WHERE asesor_slug=%s ORDER BY fecha DESC, hora DESC, id DESC', (u['user'],), fetch=True) or []
    return _hdr(render_template('visitas.html', por_estatus=por_estatus, propias=propias, act=_fecha_corta(act), err=err,
                                ok='Visita registrada.' if request.args.get('ok') == '1' else '', csrf=portal_lib.csrf_token(u),
                                puede=u['role'] != 'admin'), 400 if err else 200)


def _fecha_valida(s):
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return True
    except ValueError:
        return False


# --- Ofertas
@app.route('/ofertas', methods=['GET', 'POST'])
@require_login
def ofertas():
    u = g.user
    err = ''
    if request.method == 'POST':
        if u['role'] == 'admin':
            return _forbidden()
        f = request.form
        tipo = f.get('tipo')
        cliente = _txt(f, 'cliente', 120)
        fecha = _txt(f, 'fecha', 10)
        if not _csrf_ok():
            err = 'La sesión expiró. Vuelve a intentar.'
        elif tipo not in ('recibida', 'realizada'):
            err = 'Indica si la oferta es recibida o realizada.'
        elif len(cliente) < 2:
            err = 'Indica el cliente.'
        elif fecha and not _fecha_valida(fecha):
            err = 'La fecha no es válida.'
        else:
            q('INSERT INTO asesor_ofertas (asesor_slug, tipo, cliente, inmueble, monto, fecha, nota, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
              (u['user'], tipo, cliente, _txt(f, 'inmueble', 160), _txt(f, 'monto', 40), fecha, (f.get('nota') or '').strip()[:400], now_iso()))
            try:
                import crm_write
                crm_write.insert_offer(q, {
                    'asesor_slug': u['user'], 'tipo': tipo, 'cliente': cliente,
                    'inmueble': _txt(f, 'inmueble', 160), 'monto': _txt(f, 'monto', 40),
                    'fecha': fecha, 'nota': (f.get('nota') or '').strip()[:400], 'source': 'portal',
                })
                _live_cache['t'] = 0.0
            except Exception:
                log.exception('crm oferta')
            return redirect(f'/ofertas?t={tipo}&ok=1')
    t = request.args.get('t') or 'recibidas'
    t = 'realizada' if t.startswith('realizada') else 'recibida'
    regs = q('SELECT * FROM asesor_ofertas WHERE asesor_slug=%s AND tipo=%s ORDER BY fecha DESC, id DESC', (u['user'], t), fetch=True) or []
    cli, act = _clientes_estatus(u['user'])
    de_estatus = [c for c in cli if c['estatus'] in ('Oferta realizada', 'Oferta aceptada')] if t == 'realizada' else []
    return _hdr(render_template('ofertas.html', t=t, regs=regs, de_estatus=de_estatus, act=_fecha_corta(act), err=err,
                                ok='Oferta registrada.' if request.args.get('ok') == '1' else '', csrf=portal_lib.csrf_token(u),
                                puede=u['role'] != 'admin'), 400 if err else 200)


# --- Cierres (Pendientes / Realizados), mismas reglas de comisión
@app.get('/cierres')
@require_login
def cierres():
    u = g.user
    if u['role'] == 'admin':
        items = portal_lib.load_cierres(full=True)
        for c in items:
            c['fecha_fmt'] = portal_lib.fmt_fecha(c.get('fecha') or '')
            calc = c.get('calc') or {}
            c['asesor_fmt'] = portal_lib.money(calc.get('asesor'))
            c['forrent_fmt'] = portal_lib.money(calc.get('forrent'))
            c['inmobiliaria_fmt'] = portal_lib.money(calc.get('inmobiliaria')) if 'inmobiliaria' in calc else '—'
            c['asesor_nombre'] = _nombre_asesor(c.get('asesor')) if c.get('asesor') else 'Sin asesor'
    else:
        items = _cierres_vista(u, full=u['role'] == 'lider')
    t = 'realizados' if request.args.get('t') == 'realizados' else 'pendientes'
    pend = [c for c in items if (c.get('estado_pago') or '') == 'pendiente']
    real = [c for c in items if (c.get('estado_pago') or '') in ('realizada', 'realizado', 'pagado', 'pagada', 'cobrado')]
    act = portal_lib._load('cierres.json', {}).get('actualizado') or ''
    return _hdr(render_template('cierres.html', t=t, filas=real if t == 'realizados' else pend, n_pend=len(pend), n_real=len(real),
                                act=_fecha_corta(act)))


# --- Actividades
def _destinatarios(row):
    d = (row.get('destinatarios') or '').strip()
    return [a['user'] for a in portal_lib.asesores_meta()] if d == '*' else [x for x in d.split(',') if x]


def _actividades_para(slug):
    rows = q('SELECT * FROM actividades ORDER BY fecha DESC, hora DESC, id DESC', fetch=True) or []
    return [r for r in rows if slug in _destinatarios(r)]


@app.get('/actividades')
@require_login
def actividades():
    u = g.user
    if u['role'] == 'admin':
        return redirect('/admin/actividades')
    hoy = _hoy_caracas().isoformat()
    rows = _actividades_para(u['user'])
    return _hdr(render_template('actividades.html', proximas=[r for r in rows if (r['fecha'] or '') >= hoy][::-1],
                                pasadas=[r for r in rows if (r['fecha'] or '') < hoy]))


@app.route('/admin/actividades', methods=['GET', 'POST'])
@require_admin
def admin_actividades():
    err = ''
    asesoras = portal_lib.asesores_meta()
    if request.method == 'POST':
        f = request.form
        titulo, fecha, hora = _txt(f, 'titulo', 140), _txt(f, 'fecha', 10), _txt(f, 'hora', 5)
        modalidad = f.get('modalidad')
        para = f.get('para')
        sel = [s for s in f.getlist('dest') if portal_lib.asesor_by_user(s)]
        if not _csrf_ok():
            err = 'La sesión expiró. Vuelve a intentar.'
        elif len(titulo) < 3:
            err = 'Indica el título.'
        elif not _fecha_valida(fecha):
            err = 'Indica una fecha válida.'
        elif modalidad not in ('online', 'presencial'):
            err = 'Indica la modalidad.'
        elif para != 'todas' and not sel:
            err = 'Elige al menos una asesora.'
        else:
            dest = '*' if para == 'todas' else ','.join(sel)
            lugar = _txt(f, 'enlace_lugar', 300)
            q('INSERT INTO actividades (titulo, fecha, hora, modalidad, enlace_lugar, destinatarios, creado_por, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
              (titulo, fecha, hora, modalidad, lugar, dest, g.user['user'], now_iso()))
            fecha_txt = portal_lib.fmt_fecha(fecha) + (f' a las {hora}' if hora else '')
            for s in _destinatarios({'destinatarios': dest}):
                nombre = (portal_lib.asesor_by_user(s) or {}).get('name', s).split(' ')[0]
                _correo(s, 'Tienes una nueva actividad',
                        f"Hola {nombre},\n\nTienes una nueva actividad: {titulo}\nFecha: {fecha_txt} (Caracas)\n"
                        f"Modalidad: {'En línea' if modalidad == 'online' else 'Presencial'}\n"
                        + (f"{'Enlace' if modalidad == 'online' else 'Lugar'}: {lugar}\n" if lugar else '')
                        + "\nLa ves en For Rent Online › Actividades.\n\nFor Rent Online", 'actividad_nueva')
            return redirect('/admin/actividades?ok=1')
    rows = q('SELECT * FROM actividades ORDER BY fecha DESC, hora DESC, id DESC', fetch=True) or []
    for r in rows:
        r['dest_txt'] = 'Todas' if r['destinatarios'] == '*' else ', '.join(_nombre_asesor(s).split(' ')[0] for s in _destinatarios(r))
    return _hdr(render_template('admin_actividades.html', rows=rows, asesoras=asesoras, err=err, csrf=portal_lib.csrf_token(g.user),
                                ok='Actividad creada; avisos registrados en Correos.' if request.args.get('ok') == '1' else ''), 400 if err else 200)


# --- Asignaciones nuevas -> correo (sin duplicar la rutina existente)
def revisar_asignaciones_nuevas():
    """Compara la copia de asignados con las ya notificadas. La primera vez solo marca la base (sin correos).
    ASSIGN_EMAIL_MODE=omitir (por defecto): registra 'omitido' porque la rutina de la caja ya avisa por Gmail."""
    asig, _, _ = portal_lib.load_asignados()
    vistos = {(r['asesor_slug'], r['cliente_id']) for r in (q('SELECT asesor_slug, cliente_id FROM asignaciones_notificadas', fetch=True) or [])}
    base = not vistos
    modo = (os.environ.get('ASSIGN_EMAIL_MODE') or 'omitir').lower()
    nuevos = 0
    for c in asig:
        key = (c.get('asesor') or '', str(c.get('id') or ''))
        if not all(key) or key in vistos:
            continue
        q('INSERT INTO asignaciones_notificadas (asesor_slug, cliente_id, created_at) VALUES (%s,%s,%s)', key + (now_iso(),))
        vistos.add(key)
        if base:
            continue
        nuevos += 1
        nombre = (portal_lib.asesor_by_user(key[0]) or {}).get('name', key[0]).split(' ')[0]
        cuerpo = (f"Hola {nombre},\n\nTienes una nueva asignación: {c.get('nombre') or 'cliente'}"
                  + (f" ({c.get('operacion')})" if c.get('operacion') else '') + ".\nLa ves en For Rent Online › Mis Clientes.\n\nFor Rent Online")
        if modo == 'enviar':
            _correo(key[0], 'Tienes una nueva asignación', cuerpo, 'asignacion_nueva')
        else:
            _correo(key[0], 'Tienes una nueva asignación', cuerpo, 'asignacion_nueva', estado='omitido',
                    motivo='la rutina de asignación ya envía este aviso por Gmail')
    return {'base': base, 'nuevos': nuevos}


@app.route('/admin/correos', methods=['GET', 'POST'])
@require_admin
def admin_correos():
    res = None
    if request.method == 'POST':
        if not _csrf_ok():
            abort(400)
        res = revisar_asignaciones_nuevas()
    rows = q('SELECT id, to_slug, to_addr, asunto, evento, estado, motivo, created_at FROM email_outbox ORDER BY id DESC LIMIT 200', fetch=True) or []
    for r in rows:
        r['cuando'] = _fmt_time(r['created_at'])
    return _hdr(render_template('admin_correos.html', rows=rows, modo=notif.mode(), res=res, csrf=portal_lib.csrf_token(g.user)))


def _fuera_de_horario(dt_caracas):
    return not (8 <= dt_caracas.hour < 20)


def maybe_auto_reply(wa_id, pnid):
    """Una vez por contacto por noche (reemplaza al workflow de GHL «fuera de horario»; queda auto=1)."""
    if os.environ.get('AUTO_REPLY_ENABLED') != '1' or not wa_id: return
    tok, pid, txt = os.environ.get('META_WA_TOKEN'), os.environ.get('WA_PHONE_NUMBER_ID') or pnid, os.environ.get('AUTO_REPLY_TEXT')
    if not (tok and pid and txt): log.warning('Auto-respuesta activada pero faltan META_WA_TOKEN/AUTO_REPLY_TEXT'); return
    now = datetime.now(CARACAS)
    if not _fuera_de_horario(now): return
    inicio = now.replace(hour=20, minute=0, second=0, microsecond=0)  # la noche empieza 8 p. m. Caracas
    if now.hour < 20: inicio -= timedelta(days=1)
    corte_utc = inicio.astimezone(timezone.utc).isoformat()
    ya = q("SELECT 1 FROM wa_events WHERE kind='outbound' AND auto=1 AND wa_id=%s AND ts >= %s LIMIT 1", (wa_id, corte_utc), fetch=True)
    if ya: return
    ver = os.environ.get('GRAPH_VERSION', 'v27.0')
    body = json.dumps({'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': wa_id,
                       'type': 'text', 'text': {'preview_url': False, 'body': txt}}).encode()
    req = urllib.request.Request(f'https://graph.facebook.com/{ver}/{pid}/messages', data=body, method='POST',
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            wamid = (json.loads(r.read()).get('messages') or [{}])[0].get('id')
        insert_event(kind='outbound', wamid=wamid, wa_id=wa_id, direction='outbound', msg_type='text', text=txt,
                     ts=now_iso(), status='', auto=1, source='auto_fuera_horario', data={})
        log.info('Auto-respuesta fuera de horario enviada')
    except Exception as e:
        log.error('Auto-respuesta falló: %s', type(e).__name__)


init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
