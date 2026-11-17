"""Planificador de redes sociales propio de For Rent Online (sin GHL).

Tablas nuevas y aditivas (CREATE TABLE IF NOT EXISTS; no toca tablas existentes):
- social_accounts: cuentas de redes de For Rent (clave «red:nombre»).
- social_posts: publicaciones (Borrador/Programado/Publicado/Error/En revisión/Pendiente), con autor y aprobación.
  Se borran de forma lógica (deleted_at), nunca físicamente.
- social_media: archivos subidos desde el portal (imagen/video) guardados en la base.
- social_meta: marcas internas (p. ej. «importación GHL hecha»).

Importante: aquí NO hay código que publique en redes. Lo programado se queda en la base.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Toronto')
ESTADOS = ('Publicado', 'Error', 'Programado', 'Borrador', 'En revisión', 'Pendiente')
TIPOS = ('Editor de publicaciones', 'Publicación recurrente', 'Publicación CSV')
APROBACIONES = ('Aprobado', 'Rechazado')
REDES = ('facebook', 'instagram', 'tiktok', 'pinterest')
TEXTO_MAX = 5000
MEDIA_TIPOS = {b'\xff\xd8\xff': 'image/jpeg', b'\x89PNG\r\n\x1a\n': 'image/png'}
IMG_MAX = 8 * 1024 * 1024
VID_MAX = 25 * 1024 * 1024
GHL_ESTADO = {'published': 'Publicado', 'failed': 'Error', 'scheduled': 'Programado', 'draft': 'Borrador',
              'in_review': 'En revisión', 'pending': 'Pendiente'}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    blob = 'BYTEA' if is_pg else 'BLOB'
    q(f"""CREATE TABLE IF NOT EXISTS social_accounts (
        id {serial}, clave TEXT NOT NULL UNIQUE, red TEXT NOT NULL, nombre TEXT NOT NULL, ext_id TEXT,
        activa INTEGER NOT NULL DEFAULT 1, origen TEXT, created_at TEXT NOT NULL,
        publicar_auto INTEGER NOT NULL DEFAULT 0)""")  # interruptor por cuenta: 0 = apagado (todas nacen apagadas)
    q(f"""CREATE TABLE IF NOT EXISTS social_posts (
        id {serial}, origen TEXT NOT NULL, ext_id TEXT, estado TEXT NOT NULL, tipo TEXT NOT NULL,
        texto TEXT NOT NULL DEFAULT '', media TEXT NOT NULL DEFAULT '[]', cuentas TEXT NOT NULL DEFAULT '[]',
        programado_local TEXT, programado_utc TEXT,
        creado_por TEXT, creado_por_nombre TEXT, aprobacion TEXT, aprobador TEXT, aprobador_nombre TEXT, aprobado_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, actualizado_por TEXT, deleted_at TEXT, deleted_by TEXT)""")
    try:  # por si la tabla ya existía sin el interruptor (aditivo; todas quedan apagadas)
        q('ALTER TABLE social_accounts ADD COLUMN publicar_auto INTEGER NOT NULL DEFAULT 0')
    except Exception as e:
        if 'exist' not in str(e).lower() and 'duplicate' not in str(e).lower():
            raise
    q('CREATE INDEX IF NOT EXISTS social_posts_prog ON social_posts (programado_local)')
    q(f"""CREATE TABLE IF NOT EXISTS social_media (
        id {serial}, mime TEXT NOT NULL, nombre TEXT, tam INTEGER NOT NULL, datos {blob} NOT NULL,
        subido_por TEXT, created_at TEXT NOT NULL)""")
    q('CREATE TABLE IF NOT EXISTS social_meta (clave TEXT PRIMARY KEY, valor TEXT, at TEXT)')


# ------------------------------------------------------------------ cuentas
def cuentas(q, solo_activas=True):
    rows = q('SELECT clave, red, nombre, activa FROM social_accounts' + (' WHERE activa=1' if solo_activas else ''), fetch=True) or []
    orden = {r: i for i, r in enumerate(REDES)}
    return sorted(({'id': r['clave'], 'red': r['red'], 'nombre': r['nombre']} for r in rows),
                  key=lambda c: (orden.get(c['red'], 9), c['nombre'].lower()))


def _cuenta_upsert(q, clave, origen):
    red, _, nombre = clave.partition(':')
    if not nombre:
        red, nombre = '', red
    if q('SELECT 1 AS x FROM social_accounts WHERE clave=%s', (clave,), fetch=True):
        return
    q('INSERT INTO social_accounts (clave, red, nombre, origen, created_at) VALUES (%s,%s,%s,%s,%s)',
      (clave, red.strip().lower(), nombre.strip(), origen, now_iso()))


# ------------------------------------------------------------------ importación única del export GHL
def importar_ghl_una_vez(q, payload, por='sistema'):
    """Copia las publicaciones de planificador.json (export GHL) a social_posts UNA sola vez.
    No modifica el JSON (queda como respaldo en portal_datos). Devuelve cuántas se importaron (0 si ya se hizo)."""
    if q("SELECT 1 AS x FROM social_meta WHERE clave='ghl_import'", fetch=True):
        return 0
    posts = (payload or {}).get('posts') or []
    if not posts:
        return 0
    ahora = now_iso()
    try:  # «reclamo» atómico: si dos visitas llegan a la vez, solo una importa (social_meta.clave es PRIMARY KEY)
        q('INSERT INTO social_meta (clave, valor, at) VALUES (%s,%s,%s)', ('ghl_import', '{"estado": "en curso"}', ahora))
    except Exception:
        return 0
    try:
        # Pocas consultas (la app abre una conexión por consulta): se leen las existentes una vez y se inserta por lotes.
        ya = {r['ext_id'] for r in (q("SELECT ext_id FROM social_posts WHERE origen='ghl_import'", fetch=True) or [])}
        cuentas_ya = {r['clave'] for r in (q('SELECT clave FROM social_accounts', fetch=True) or [])}
        filas, nuevas_cuentas = [], []
        for p in posts:
            if not isinstance(p, dict):
                continue
            ext = str(p.get('post_id') or '').strip()
            if ext and ext in ya:
                continue  # duplicado (el export trae alguna fila repetida) o ya importado
            if ext:
                ya.add(ext)
            cs = [c.strip() for c in str(p.get('cuentas') or '').split('|') if c.strip()]
            for c in cs:
                if c not in cuentas_ya:
                    cuentas_ya.add(c)
                    nuevas_cuentas.append(c)
            media = [m.strip() for m in str(p.get('media_urls') or '').split('|') if m.strip().startswith('https://')]
            filas.append(('ghl_import', ext, GHL_ESTADO.get(str(p.get('status') or 'scheduled').lower(), 'Programado'), TIPOS[0],
                          str(p.get('texto') or ''), json.dumps(media), json.dumps(cs, ensure_ascii=False),
                          str(p.get('fecha_hora_toronto') or '')[:16], str(p.get('fecha_hora_utc') or ''),
                          'ghl', 'Importado de GHL', ahora, ahora))
        for c in nuevas_cuentas:
            _cuenta_upsert(q, c, 'ghl_import')
        for i in range(0, len(filas), 50):
            lote = filas[i:i + 50]
            q("""INSERT INTO social_posts (origen, ext_id, estado, tipo, texto, media, cuentas, programado_local, programado_utc,
                 creado_por, creado_por_nombre, created_at, updated_at) VALUES """ +
              ','.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(lote)), tuple(v for f in lote for v in f))
        n = len(filas)
    except Exception:
        # Se suelta el reclamo para reintentar en la próxima visita; lo ya insertado no se repite (se salta por ext_id).
        q("DELETE FROM social_meta WHERE clave='ghl_import'")
        raise
    q('UPDATE social_meta SET valor=%s, at=%s WHERE clave=%s',
      (json.dumps({'importadas': n, 'por': por, 'fuente': (payload or {}).get('fuente') or ''}), ahora, 'ghl_import'))
    return n


# ------------------------------------------------------------------ lectura
VIDEO_EXT = re.compile(r'\.(mp4|mov|webm|m4v)(\?|#|$)', re.I)


def _media_item(m, mimes):
    if m.startswith('https://'):
        return {'url': m, 'video': bool(VIDEO_EXT.search(m)), 'ref': m}
    if m.startswith('media:') and m[6:].isdigit():
        mid = int(m[6:])
        return {'url': f'/mercadeo/planificador/media/{mid}', 'video': mimes.get(mid, '').startswith('video/'), 'ref': m}
    return None


def _mimes(q):
    return {int(r['id']): r['mime'] for r in (q('SELECT id, mime FROM social_media', fetch=True) or [])}


def _fila(r, mimes):
    media = [x for x in (_media_item(m, mimes) for m in json.loads(r['media'] or '[]') if isinstance(m, str)) if x]
    return {
        'id': r['id'], 'origen': r['origen'], 'estado': r['estado'], 'tipo': r['tipo'], 'texto': r['texto'] or '',
        'media': media,
        'media_raw': [x['ref'] for x in media],
        'cuentas': json.loads(r['cuentas'] or '[]'),
        'cuando': r['programado_local'] or '', 'fecha': (r['programado_local'] or '')[:10],
        'creado_por': r['creado_por_nombre'] or r['creado_por'] or '', 'aprobacion': r['aprobacion'] or '',
        'aprobador': r['aprobador_nombre'] or r['aprobador'] or '', 'thumb': '',
    }


def listar(q):
    rows = q('SELECT * FROM social_posts WHERE deleted_at IS NULL ORDER BY programado_local, id', fetch=True) or []
    mimes = _mimes(q)
    return [_fila(r, mimes) for r in rows]


def uno(q, pid, incluir_borrados=False):
    rows = q('SELECT * FROM social_posts WHERE id=%s' + ('' if incluir_borrados else ' AND deleted_at IS NULL'), (pid,), fetch=True)
    return _fila(rows[0], _mimes(q)) if rows else None


# ------------------------------------------------------------------ escritura
def validar(form_vals, claves_validas):
    """form_vals: dict con texto, fecha (YYYY-MM-DD), hora (HH:MM), cuentas (lista), accion (borrador|programar),
    media (lista ya validada). Devuelve (datos, error)."""
    texto = (form_vals.get('texto') or '').replace('\r\n', '\n').strip()
    if len(texto) > TEXTO_MAX:
        return None, f'El texto supera {TEXTO_MAX} caracteres.'
    cs = [c for c in form_vals.get('cuentas') or [] if c in claves_validas]
    accion = form_vals.get('accion')
    estado = 'Programado' if accion == 'programar' else 'Borrador'
    fecha, hora = (form_vals.get('fecha') or '').strip(), (form_vals.get('hora') or '').strip()
    local = utc = ''
    if fecha:
        try:
            dt = datetime.strptime(f'{fecha} {hora or "09:00"}', '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        except ValueError:
            return None, 'La fecha u hora no es válida.'
        local, utc = dt.strftime('%Y-%m-%d %H:%M'), dt.astimezone(timezone.utc).isoformat(timespec='seconds')
    if estado == 'Programado':
        if not cs:
            return None, 'Elige al menos una cuenta para programar.'
        if not local:
            return None, 'Pon la fecha y hora para programar.'
        if not texto and not form_vals.get('media'):
            return None, 'Escribe un texto o agrega un archivo.'
        if local < datetime.now(TZ).strftime('%Y-%m-%d %H:%M'):
            return None, 'La fecha y hora ya pasaron.'
    elif not (texto or form_vals.get('media') or cs):
        return None, 'El borrador está vacío.'
    return {'texto': texto, 'cuentas': cs, 'estado': estado, 'local': local, 'utc': utc,
            'media': form_vals.get('media') or []}, None


def crear(q, d, user):
    ahora = now_iso()
    r = q("""INSERT INTO social_posts (origen, estado, tipo, texto, media, cuentas, programado_local, programado_utc,
           creado_por, creado_por_nombre, created_at, updated_at, actualizado_por)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          ('portal', d['estado'], TIPOS[0], d['texto'], json.dumps(d['media']), json.dumps(d['cuentas'], ensure_ascii=False),
           d['local'], d['utc'], user['user'], user.get('name') or user['user'], ahora, ahora, user['user']), fetch=True)
    return r[0]['id']


def editar(q, pid, d, user):
    q("""UPDATE social_posts SET estado=%s, texto=%s, media=%s, cuentas=%s, programado_local=%s, programado_utc=%s,
         aprobacion=NULL, aprobador=NULL, aprobador_nombre=NULL, aprobado_at=NULL, updated_at=%s, actualizado_por=%s
         WHERE id=%s AND deleted_at IS NULL""",
      (d['estado'], d['texto'], json.dumps(d['media']), json.dumps(d['cuentas'], ensure_ascii=False), d['local'], d['utc'],
       now_iso(), user['user'], pid))


def duplicar(q, pid, user):
    rows = q('SELECT * FROM social_posts WHERE id=%s AND deleted_at IS NULL', (pid,), fetch=True)
    if not rows:
        return None
    r, ahora = rows[0], now_iso()
    n = q("""INSERT INTO social_posts (origen, estado, tipo, texto, media, cuentas, programado_local, programado_utc,
           creado_por, creado_por_nombre, created_at, updated_at, actualizado_por)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          ('portal', 'Borrador', r['tipo'], r['texto'], r['media'], r['cuentas'], r['programado_local'], r['programado_utc'],
           user['user'], user.get('name') or user['user'], ahora, ahora, user['user']), fetch=True)
    return n[0]['id']


def borrar(q, pid, user):
    q('UPDATE social_posts SET deleted_at=%s, deleted_by=%s WHERE id=%s AND deleted_at IS NULL', (now_iso(), user['user'], pid))


def aprobar(q, pid, valor, user):
    q('UPDATE social_posts SET aprobacion=%s, aprobador=%s, aprobador_nombre=%s, aprobado_at=%s WHERE id=%s AND deleted_at IS NULL',
      (valor, user['user'], user.get('name') or user['user'], now_iso(), pid))


def media_mime(data, filename=''):
    for sig, mime in MEDIA_TIPOS.items():
        if data.startswith(sig):
            return mime
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[4:8] == b'ftyp':
        return 'video/mp4'
    return None


def guardar_media(q, data, mime, nombre, user):
    r = q('INSERT INTO social_media (mime, nombre, tam, datos, subido_por, created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
          (mime, (nombre or '')[:120], len(data), data, user['user'], now_iso()), fetch=True)
    return r[0]['id']


def leer_media(q, mid):
    rows = q('SELECT mime, datos FROM social_media WHERE id=%s', (mid,), fetch=True)
    return rows[0] if rows else None
