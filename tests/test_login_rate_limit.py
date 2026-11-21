"""Pruebas del límite de intentos fallidos en POST /login (SQLite local, sin red)."""
import pytest

from test_bandeja import appmod, portal_lib

IP_A = '203.0.113.10'
IP_B = '203.0.113.20'
ADMIN_LOGIN = 'pintojosedaniel'


def entra(usuario, password, ip=IP_A, headers=None):
    c = appmod.app.test_client()
    h = {'CF-Connecting-IP': ip}
    if headers:
        h.update(headers)
    return c, c.post('/login', data={'usuario': usuario, 'password': password}, headers=h)


@pytest.fixture(autouse=True)
def limpia_fallos():
    appmod.q('DELETE FROM login_fails')
    yield
    appmod.q('DELETE FROM login_fails')


def test_tabla_login_fails_existe():
    rows = appmod.q(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='login_fails'", fetch=True)
    assert rows and rows[0]['name'] == 'login_fails'


def test_get_login_no_cuenta_ni_bloquea():
    c = appmod.app.test_client()
    for _ in range(6):
        r = c.get('/login', headers={'CF-Connecting-IP': IP_A})
        assert r.status_code == 200
        assert 'Demasiados intentos' not in r.get_data(as_text=True)


def test_cinco_fallos_bloquean_el_sexto():
    for _ in range(5):
        c, r = entra(ADMIN_LOGIN, 'mala')
        assert r.status_code == 200
        assert 'Usuario o contraseña incorrectos.' in r.get_data(as_text=True)
    c, r = entra(ADMIN_LOGIN, 'mala')
    texto = r.get_data(as_text=True)
    assert r.status_code == 429
    assert 'Demasiados intentos. Intenta de nuevo en' in texto
    assert 'minutos' in texto
    assert 'incorrectos' not in texto
    # Aunque la contraseña sea la buena, sigue bloqueado y no abre sesión.
    c, r = entra(ADMIN_LOGIN, 'clave-jose')
    assert r.status_code == 429
    assert c.get('/portal', headers={'CF-Connecting-IP': IP_A}).status_code == 302


def test_bloqueo_no_revela_si_el_usuario_existe():
    for _ in range(5):
        entra('noexiste', 'mala')
    _, r_falso = entra('noexiste', 'mala')
    for _ in range(5):
        entra(ADMIN_LOGIN, 'mala', ip=IP_B)
    _, r_real = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_B)
    assert r_falso.status_code == r_real.status_code == 429
    for r in (r_falso, r_real):
        texto = r.get_data(as_text=True)
        assert 'Demasiados intentos. Intenta de nuevo en' in texto
        assert 'incorrectos' not in texto
        assert 'no existe' not in texto.lower()
        assert 'no encontrado' not in texto.lower()


def test_exito_limpia_el_contador_de_ese_usuario():
    for _ in range(4):
        _, r = entra(ADMIN_LOGIN, 'mala')
        assert r.status_code == 200
    c, r = entra(ADMIN_LOGIN, 'clave-jose')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/portal')
    # Tras el éxito (y en un cliente nuevo, sin sesión), un fallo vuelve a ser 200, no 429.
    _, r = entra(ADMIN_LOGIN, 'mala')
    assert r.status_code == 200
    assert 'incorrectos' in r.get_data(as_text=True)


def test_bloqueo_es_por_usuario_e_ip_no_global_por_usuario():
    for _ in range(5):
        entra(ADMIN_LOGIN, 'mala', ip=IP_A)
    _, r = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_A)
    assert r.status_code == 429
    _, r = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_B)
    assert r.status_code == 302


def test_misma_ip_otro_usuario_no_hereda_el_tope_de_cinco():
    for _ in range(5):
        entra('ana', 'mala', ip=IP_A)
    _, r = entra('ana', 'clave-ana', ip=IP_A)
    assert r.status_code == 429
    _, r = entra(ADMIN_LOGIN, 'mala', ip=IP_A)
    assert r.status_code == 200
    assert 'incorrectos' in r.get_data(as_text=True)
    _, r = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_A)
    assert r.status_code == 302


def test_veinte_fallos_de_la_misma_ip_bloquean_cualquier_usuario():
    for i in range(appmod.LOGIN_FAIL_MAX_IP):
        _, r = entra(f'user{i}', 'mala', ip=IP_A)
        assert r.status_code == 200, i
    _, r = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_A)
    assert r.status_code == 429
    assert 'Demasiados intentos. Intenta de nuevo en' in r.get_data(as_text=True)


def test_cf_connecting_ip_pesa_mas_que_x_forwarded_for():
    for _ in range(5):
        entra(ADMIN_LOGIN, 'mala', ip=IP_A)
    # XFF apunta a la IP bloqueada, pero Cloudflare dice que es otra: no debe bloquear.
    _, r = entra(ADMIN_LOGIN, 'clave-jose', ip=IP_B, headers={'X-Forwarded-For': IP_A})
    assert r.status_code == 302


def test_sin_cf_usa_el_primer_xff_valido():
    c = appmod.app.test_client()
    h = {'X-Forwarded-For': f'{IP_A}, 10.0.0.1'}
    for _ in range(5):
        r = c.post('/login', data={'usuario': ADMIN_LOGIN, 'password': 'mala'}, headers=h)
        assert r.status_code == 200
    r = c.post('/login', data={'usuario': ADMIN_LOGIN, 'password': 'mala'}, headers=h)
    assert r.status_code == 429
    # Otra IP en XFF no está bloqueada.
    r = c.post('/login', data={'usuario': ADMIN_LOGIN, 'password': 'clave-jose'},
               headers={'X-Forwarded-For': IP_B})
    assert r.status_code == 302


def test_cabecera_ip_invalida_se_ignora():
    assert appmod._valid_ip('not-an-ip') == ''
    assert appmod._valid_ip('203.0.113.10') == '203.0.113.10'
    assert appmod._valid_ip('203.0.113.10:8080') == '203.0.113.10'
    c = appmod.app.test_client()
    malo = {'CF-Connecting-IP': 'no-es-ip', 'X-Forwarded-For': IP_A}
    for _ in range(5):
        r = c.post('/login', data={'usuario': ADMIN_LOGIN, 'password': 'mala'}, headers=malo)
        assert r.status_code == 200
    r = c.post('/login', data={'usuario': ADMIN_LOGIN, 'password': 'mala'}, headers=malo)
    assert r.status_code == 429


def test_identify_user_sigue_igual():
    assert portal_lib.identify_user(ADMIN_LOGIN, 'clave-jose')['role'] == 'admin'
    assert portal_lib.identify_user(ADMIN_LOGIN, 'mala') is None
    assert portal_lib.identify_user('ana', 'clave-ana')['role'] == 'lider'
    assert portal_lib.identify_user('', 'clave-jose') is None


def test_admin_entra_con_usuario_nuevo_y_conserva_slug():
    found = portal_lib.identify_user(ADMIN_LOGIN, 'clave-jose')
    assert found['user'] == 'jose' and found['role'] == 'admin'
    assert portal_lib.identify_user('PintoJoseDaniel', 'clave-jose')['user'] == 'jose'
    assert portal_lib.identify_user('jose', 'clave-jose') is None
    assert portal_lib.user_by_username('jose')['role'] == 'admin'
    assert portal_lib.user_by_username(ADMIN_LOGIN) is None


def test_panel_username_se_puede_cambiar(monkeypatch):
    monkeypatch.setenv('PANEL_USERNAME', 'otro-admin')
    assert portal_lib.identify_user('otro-admin', 'clave-jose')['user'] == 'jose'
    assert portal_lib.identify_user(ADMIN_LOGIN, 'clave-jose') is None
    assert portal_lib.identify_user('jose', 'clave-jose') is None
    c, r = entra('otro-admin', 'clave-jose')
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert s.get('uid') == 'jose'
