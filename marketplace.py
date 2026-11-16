"""Enlaces de Facebook Marketplace por inmueble (Redes Sociales de Mercadeo).

Tablas nuevas y aditivas: ``crm_marketplace_links`` (un enlace por inmueble) y ``crm_marketplace_links_log``
(bitácora: quién, cuándo, antes/después). Solo usuarios con rol admin (José, Luz) agregan, cambian o quitan.
Nunca se inventan enlaces: casilla vacía hasta que alguien pegue la publicación real.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Caracas')
HOSTS = ('facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com')
NOMBRE = 'Facebook Marketplace'
LOGO = 'redes/marketplace.svg'


def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


def init(q, is_pg):
    serial = 'BIGSERIAL PRIMARY KEY' if is_pg else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    q("""CREATE TABLE IF NOT EXISTS crm_marketplace_links (
        inmueble_key TEXT PRIMARY KEY, inmueble_codigo TEXT, url TEXT NOT NULL,
        creado_por TEXT, created_at TEXT NOT NULL, actualizado_por TEXT, updated_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS crm_marketplace_links_log (
        id {serial}, inmueble_key TEXT NOT NULL, accion TEXT NOT NULL,
        url_antes TEXT, url_despues TEXT, por TEXT, at TEXT NOT NULL)""")


def url_valida(raw):
    """(url_normalizada, error). Solo https://(www.|m.|web.)facebook.com/marketplace/... sin parámetros de rastreo."""
    u = (raw or '').strip()
    if not u:
        return None, 'Pega el enlace de la publicación en Facebook Marketplace.'
    if len(u) > 500:
        return None, 'El enlace es demasiado largo.'
    try:
        p = urlparse(u)
    except Exception:
        return None, 'El enlace no es válido.'
    if p.scheme != 'https' or (p.hostname or '').lower() not in HOSTS or p.username or p.password or p.port:
        return None, 'El enlace debe ser https://www.facebook.com/marketplace/…'
    path = p.path or ''
    if not (path == '/marketplace' or path.startswith('/marketplace/')) or path.rstrip('/') == '/marketplace':
        return None, 'El enlace debe ser una publicación de Facebook Marketplace (facebook.com/marketplace/item/…).'
    return urlunparse(('https', 'www.facebook.com', path, '', '', '')), None


def links_por_key(q):
    rows = q('SELECT inmueble_key, url, actualizado_por, updated_at FROM crm_marketplace_links', fetch=True) or []
    return {r['inmueble_key']: r for r in rows}


def _log(q, key, accion, antes, despues, por):
    q('INSERT INTO crm_marketplace_links_log (inmueble_key, accion, url_antes, url_despues, por, at) '
      'VALUES (%s,%s,%s,%s,%s,%s)', (key, accion, antes, despues, por, now_iso()))


def guardar(q, *, key, codigo, url, por, is_pg):
    prev = q('SELECT url FROM crm_marketplace_links WHERE inmueble_key=%s', (key,), fetch=True) or []
    ahora = now_iso()
    if prev:
        if prev[0]['url'] == url:
            return 'sin_cambio'
        q('UPDATE crm_marketplace_links SET url=%s, actualizado_por=%s, updated_at=%s WHERE inmueble_key=%s',
          (url, por, ahora, key))
        _log(q, key, 'reemplazo', prev[0]['url'], url, por)
        return 'reemplazo'
    q('INSERT INTO crm_marketplace_links (inmueble_key, inmueble_codigo, url, creado_por, created_at, actualizado_por, updated_at) '
      'VALUES (%s,%s,%s,%s,%s,%s,%s)', (key, codigo, url, por, ahora, por, ahora))
    _log(q, key, 'alta', None, url, por)
    return 'alta'


def quitar(q, *, key, por):
    prev = q('SELECT url FROM crm_marketplace_links WHERE inmueble_key=%s', (key,), fetch=True) or []
    if not prev:
        return False
    q('DELETE FROM crm_marketplace_links WHERE inmueble_key=%s', (key,))
    _log(q, key, 'baja', prev[0]['url'], None, por)
    return True


def historial(q, key, limite=10):
    return q('SELECT accion, url_antes, url_despues, por, at FROM crm_marketplace_links_log WHERE inmueble_key=%s '
             'ORDER BY id DESC LIMIT %s', (key, limite), fetch=True) or []
