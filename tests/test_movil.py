"""F1/F2/F4 móvil: guard de doble envío (estatus y Captaciones) y caché de estáticos versionados. SQLite local, sin red."""
import hashlib, json, os, re, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if 'app' not in sys.modules:
    _tmp = tempfile.mkdtemp()
    os.environ['SQLITE_PATH'] = os.path.join(_tmp, 't.db')
    os.environ.pop('DATABASE_URL', None)
    os.environ['PANEL_PASSWORD'] = 'clave-jose'
    os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest(),
                                                       'luz': hashlib.sha256(b'clave-luz').hexdigest()})
    for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX', 'META_PAGE_TOKEN'):
        os.environ.pop(k, None)
    os.chdir(_tmp)
os.environ['EMAIL_MODE'] = 'log'

import app as appmod  # noqa: E402
import crm_write, portal_lib  # noqa: E402


def cliente(slug):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s['uid'] = slug
    return c


def csrf(slug):
    return portal_lib.csrf_token(portal_lib.user_by_username(slug))


def n(sql):
    return appmod.q(sql, fetch=True)[0]['n']


@pytest.fixture
def propio():
    now = appmod.now_iso()
    tel = '5841277' + str(abs(hash(now)))[:5]
    appmod.q('INSERT INTO asesor_clientes (asesor_slug, nombre, telefono, operacion, tipo, zona, presupuesto, origen_nota, status, created_at, updated_at) '
             'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', ('ana', 'Cliente Prueba', tel, 'Alquiler', 'Apto', 'Chacao', 'US$ 800', '', 'Nuevo', now, now))
    return appmod.q('SELECT id FROM asesor_clientes WHERE telefono=%s', (tel,), fetch=True)[0]['id']


@pytest.fixture
def crm_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(crm_write, 'update_propio_status', lambda q, cid, st: calls.append((cid, st)))
    return calls


def status_de(cid):
    return appmod.q('SELECT status FROM asesor_clientes WHERE id=%s', (cid,), fetch=True)[0]['status']


# ------------------------------------------------------------ F2: estatus
def test_formulario_estatus_lleva_token(propio):
    html = cliente('ana').get('/mis-clientes').get_data(as_text=True)
    toks = re.findall(r'name="idem" value="([^"]+)"', html)
    assert toks and len(set(toks)) == len(toks)


def test_doble_envio_estatus_se_aplica_una_vez(propio, crm_calls):
    c = cliente('ana')
    d = {'csrf': csrf('ana'), 'status': 'Contactado', 'idem': 'tok-estatus-1'}
    r1 = c.post(f'/asesor/clientes/{propio}/estatus', data=d)
    r2 = c.post(f'/asesor/clientes/{propio}/estatus', data=dict(d, status='Buscando'))
    assert r1.status_code == 302 and r2.status_code == 302
    assert r1.headers['Location'].endswith('/mis-clientes?ok=estatus') and r2.headers['Location'] == r1.headers['Location']
    assert status_de(propio) == 'Contactado'
    assert crm_calls == [(propio, 'Contactado')]


def test_estatus_sin_token_funciona_como_antes(propio, crm_calls):
    c = cliente('ana')
    for st in ('Contactado', 'Buscando'):
        assert c.post(f'/asesor/clientes/{propio}/estatus', data={'csrf': csrf('ana'), 'status': st}).status_code == 302
    assert status_de(propio) == 'Buscando' and len(crm_calls) == 2


def test_token_no_se_gasta_si_falla_la_validacion(propio, crm_calls):
    c = cliente('ana')
    r = c.post(f'/asesor/clientes/{propio}/estatus', data={'csrf': csrf('ana'), 'status': 'Inventado', 'idem': 'tok-val'})
    assert r.status_code != 302
    r = c.post(f'/asesor/clientes/{propio}/estatus', data={'csrf': csrf('ana'), 'status': 'Esperando', 'idem': 'tok-val'})
    assert r.status_code == 302 and status_de(propio) == 'Esperando'


def test_permisos_intactos_con_token(propio, crm_calls):
    otra = 'cristina'
    u = portal_lib.user_by_username(otra)
    assert u and not portal_lib.can_edit_propio(u, 'ana')
    r = cliente(otra).post(f'/asesor/clientes/{propio}/estatus', data={'csrf': csrf(otra), 'status': 'Contactado', 'idem': 'tok-ajeno'})
    assert r.status_code == 403 and status_de(propio) == 'Nuevo' and not crm_calls


def test_csrf_invalido_sigue_rechazado(propio, crm_calls):
    r = cliente('ana').post(f'/asesor/clientes/{propio}/estatus', data={'csrf': 'x', 'status': 'Contactado', 'idem': 'tok-csrf'})
    assert r.status_code != 302 and status_de(propio) == 'Nuevo'


# ------------------------------------------------------------ F2: Captaciones (un solo correo)
def test_doble_envio_captacion_un_solo_registro_y_correo():
    c = cliente('ana')
    html = c.get('/mis-captaciones').get_data(as_text=True)
    assert 'name="idem"' in html
    s0, e0 = n('SELECT COUNT(*) AS n FROM captacion_solicitudes'), n('SELECT COUNT(*) AS n FROM email_outbox')
    d = {'csrf': csrf('ana'), 'accion': 'alta', 'titulo': 'Apartamento prueba doble', 'operacion': 'Venta', 'origen': 'propio', 'idem': 'tok-cap-1'}
    r1 = c.post('/mis-captaciones', data=d)
    s1, e1 = n('SELECT COUNT(*) AS n FROM captacion_solicitudes'), n('SELECT COUNT(*) AS n FROM email_outbox')
    r2 = c.post('/mis-captaciones', data=d)
    assert r1.status_code == 302 and r2.status_code == 302 and r2.headers['Location'].endswith('/mis-captaciones?ok=1')
    assert s1 - s0 == 1 and e1 > e0
    assert n('SELECT COUNT(*) AS n FROM captacion_solicitudes') == s1
    assert n('SELECT COUNT(*) AS n FROM email_outbox') == e1


def test_captacion_sin_token_funciona_como_antes():
    c = cliente('ana')
    s0 = n('SELECT COUNT(*) AS n FROM captacion_solicitudes')
    d = {'csrf': csrf('ana'), 'accion': 'alta', 'titulo': 'Casa prueba sin token', 'operacion': 'Venta', 'origen': 'propio'}
    c.post('/mis-captaciones', data=d); c.post('/mis-captaciones', data=d)
    assert n('SELECT COUNT(*) AS n FROM captacion_solicitudes') - s0 == 2


def test_captacion_con_error_no_gasta_token():
    c = cliente('ana')
    s0 = n('SELECT COUNT(*) AS n FROM captacion_solicitudes')
    d = {'csrf': csrf('ana'), 'accion': 'alta', 'titulo': 'ab', 'operacion': 'Venta', 'origen': 'propio', 'idem': 'tok-cap-err'}
    assert c.post('/mis-captaciones', data=d).status_code == 400
    assert c.post('/mis-captaciones', data=dict(d, titulo='Titulo corregido')).status_code == 302
    assert n('SELECT COUNT(*) AS n FROM captacion_solicitudes') - s0 == 1


def test_admin_sigue_sin_poder_solicitar():
    r = cliente('jose').post('/mis-captaciones', data={'csrf': csrf('jose'), 'accion': 'alta', 'titulo': 'x' * 10, 'idem': 'tok-adm'})
    assert r.status_code == 403


# ------------------------------------------------------------ F4: caché
def _v(name):
    return re.search(r'\?v=([0-9a-f]+)', appmod.static_v(name)).group(1)


@pytest.mark.parametrize('name', sorted(appmod.STATIC_VERSIONADOS))
def test_estatico_versionado_cache_larga(name):
    c = appmod.app.test_client()
    r = c.get(appmod.static_v(name))
    assert r.status_code == 200
    assert r.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    with open(os.path.join(appmod.app.static_folder, name), 'rb') as fh:
        assert _v(name) == hashlib.sha256(fh.read()).hexdigest()[:12]


def test_estatico_304_conserva_cache_larga():
    c = appmod.app.test_client()
    r = c.get(appmod.static_v('app.css'))
    r2 = c.get(appmod.static_v('app.css'), headers={'If-None-Match': r.headers['ETag']})
    assert r2.status_code == 304 and 'immutable' in r2.headers['Cache-Control']


@pytest.mark.parametrize('url', ['/static/app.css', '/static/app.css?v=10', '/static/app.css?v=deadbeef0000',
                                 '/static/manifest.webmanifest', '/static/icon-192.png', '/static/apple-touch-icon.png'])
def test_no_versionado_sin_cache_larga(url):
    r = appmod.app.test_client().get(url)
    assert 'immutable' not in (r.headers.get('Cache-Control') or '')


def test_html_con_datos_sigue_no_store(propio):
    for path in ('/portal', '/mis-clientes', '/mis-captaciones'):
        r = cliente('ana').get(path)
        assert r.status_code == 200 and r.headers['Cache-Control'] == 'private, no-store', path
    r = appmod.app.test_client().get('/login')
    assert r.headers['Cache-Control'] == 'private, no-store'
    html = r.get_data(as_text=True)
    assert appmod.static_v('app.css') in html and appmod.static_v('logo.webp') in html


# ------------------------------------------------------------ F1: script en <head>
def test_delegacion_de_fichas_en_head(propio):
    html = cliente('ana').get('/mis-clientes').get_data(as_text=True)
    head = html.split('</head>')[0]
    assert 'window.__cliBound=true' in head and "closest('.cli-row')" in head
    assert 'animation:fade' not in open(os.path.join(appmod.app.static_folder, 'app.css')).read().split('.kpi{')[1].split('}')[0]
