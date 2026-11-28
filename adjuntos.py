"""Adjuntos persistentes de ofertas y reservas (PDF e imágenes), en la base de datos.

En producción viven en Postgres (BYTEA) y sobreviven al redeploy. En la copia de
prueba, si no hay DATABASE_URL, se guardan en SQLite del servicio (efímero).
"""
from werkzeug.utils import secure_filename

MAX_BYTES = 5 * 1024 * 1024
MAX_N = 5
TIPOS = {
    b'%PDF': 'application/pdf',
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
}
EXT = {
    'application/pdf': 'pdf',
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/gif': 'gif',
    'image/webp': 'webp',
}

def mime_de(data, filename=''):
    if not data:
        return None
    for sig, mime in TIPOS.items():
        if data.startswith(sig):
            return mime
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def nombre_seguro(nombre, mime):
    base = secure_filename(nombre or '') or 'archivo'
    ext = EXT.get(mime) or ''
    if ext and not base.lower().endswith('.' + ext):
        base = base.rsplit('.', 1)[0] + '.' + ext
    return base[:120]


def leer_form(files):
    """Lee request.files.getlist('adjuntos'). Devuelve (error, lista de dicts)."""
    items = [f for f in (files or []) if f and getattr(f, 'filename', None)]
    if len(items) > MAX_N:
        return f'Puedes adjuntar hasta {MAX_N} archivos por vez.', []
    out = []
    for f in items:
        data = f.read(MAX_BYTES + 1)
        if not data:
            continue
        if len(data) > MAX_BYTES:
            return 'Cada archivo puede pesar hasta 5 MB.', []
        mime = mime_de(data, f.filename)
        if not mime:
            return 'Solo se aceptan PDF e imágenes (JPEG, PNG, WebP o GIF).', []
        out.append({
            'nombre': nombre_seguro(f.filename, mime),
            'mime': mime,
            'tam': len(data),
            'datos': data,
        })
    return '', out


def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    blob = 'BYTEA' if is_pg else 'BLOB'
    q(f"""CREATE TABLE IF NOT EXISTS asesor_reservas (
        id {serial}, asesor_slug TEXT NOT NULL, cliente TEXT NOT NULL, inmueble TEXT,
        monto TEXT, fecha TEXT, nota TEXT, created_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS operacion_adjuntos (
        id {serial}, tipo TEXT NOT NULL, registro_id INTEGER NOT NULL,
        nombre TEXT NOT NULL, mime TEXT NOT NULL, tam INTEGER NOT NULL,
        datos {blob} NOT NULL, subido_por TEXT NOT NULL, created_at TEXT NOT NULL)""")
    q('CREATE INDEX IF NOT EXISTS operacion_adjuntos_reg ON operacion_adjuntos (tipo, registro_id)')


def guardar(q, tipo, registro_id, archivos, por, now_iso):
    ids = []
    for a in archivos:
        rows = q(
            """INSERT INTO operacion_adjuntos (tipo, registro_id, nombre, mime, tam, datos, subido_por, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (tipo, registro_id, a['nombre'], a['mime'], a['tam'], a['datos'], por, now_iso),
            fetch=True,
        )
        if rows:
            ids.append(rows[0]['id'])
    return ids


def listar(q, tipo, registro_id):
    return q(
        """SELECT id, tipo, registro_id, nombre, mime, tam, subido_por, created_at
           FROM operacion_adjuntos WHERE tipo=%s AND registro_id=%s ORDER BY id""",
        (tipo, registro_id), fetch=True,
    ) or []


def listar_conteos(q, tipo, ids):
    if not ids:
        return {}
    ph = ','.join(['%s'] * len(ids))
    rows = q(
        f"SELECT registro_id, COUNT(*) AS n FROM operacion_adjuntos WHERE tipo=%s AND registro_id IN ({ph}) GROUP BY registro_id",
        (tipo, *ids), fetch=True,
    ) or []
    return {int(r['registro_id']): int(r['n']) for r in rows}


def uno(q, tipo, registro_id, adj_id):
    rows = q(
        """SELECT id, tipo, registro_id, nombre, mime, tam, datos, subido_por, created_at
           FROM operacion_adjuntos WHERE tipo=%s AND registro_id=%s AND id=%s""",
        (tipo, registro_id, adj_id), fetch=True,
    )
    return rows[0] if rows else None


def borrar_de(q, tipo, registro_id):
    q('DELETE FROM operacion_adjuntos WHERE tipo=%s AND registro_id=%s', (tipo, registro_id))


def fmt_tam(n):
    n = int(n or 0)
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n/1024:.1f} KB'
    return f'{n/1024/1024:.1f} MB'
