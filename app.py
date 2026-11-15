"""For Rent - receptor de webhooks de WhatsApp Cloud API (Meta).

Endpoints
  GET  /health                       -> estado (sin secretos)
  GET  /webhook                      -> handshake de verificación de Meta (hub.mode / hub.verify_token / hub.challenge)
  POST /webhook                      -> eventos de Meta (mensajes y estados), valida X-Hub-Signature-256
  GET  /api/events?since_id=N        -> (Bearer WA_READ_KEY) mensajes/estados nuevos para los scripts de la caja
  POST /api/outbound                 -> (Bearer WA_READ_KEY) registra una respuesta enviada desde la caja
  GET  /api/last_inbound?wa_id=...   -> (Bearer WA_READ_KEY) último mensaje entrante de un número

Variables de entorno
  DATABASE_URL                Postgres (Render). Si falta, usa SQLite local (solo pruebas).
  WA_VERIFY_TOKEN             token de verificación del webhook (o WA_VERIFY_TOKEN_SHA256 = sha256 hex del token)
  WA_READ_KEY                 llave Bearer de lectura (o WA_READ_KEY_SHA256 = sha256 hex de la llave)
  META_APP_SECRET             secreto de la app de Meta. Sin él NO se valida la firma (se registra un aviso).
  AUTO_REPLY_ENABLED=1        respuesta automática fuera de horario (apagada por defecto)
  AUTO_REPLY_TEXT, META_WA_TOKEN, WA_PHONE_NUMBER_ID, GRAPH_VERSION  (solo si AUTO_REPLY_ENABLED=1)
"""
import hashlib, hmac, json, logging, os, sqlite3, threading, time, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, request

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('wa')
app = Flask(__name__)
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


# ---------------------------------------------------------------- respuesta fuera de horario (APAGADA por defecto)
def _fuera_de_horario(dt_caracas):
    return not (8 <= dt_caracas.hour < 20)


def maybe_auto_reply(wa_id, pnid):
    """Una vez por contacto por noche (equivale al flujo GHL + etiqueta auto_fuera_horario)."""
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
