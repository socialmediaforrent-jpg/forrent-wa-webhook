"""Administrador «Ver como asesora»: sesión real intacta, barra, vuelta y bloqueo a asesoras."""
from test_bandeja import appmod, cliente, csrf, portal_lib

ADMIN = 'jose'


def _audit():
    return appmod.q(
        'SELECT admin_slug, view_as, action, method, path FROM ver_como_audit ORDER BY id',
        fetch=True) or []


def _limpia():
    appmod.q('DELETE FROM ver_como_audit')
    appmod.q('DELETE FROM email_outbox')


def test_tabla_ver_como_audit_existe():
    rows = appmod.q(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ver_como_audit'", fetch=True)
    assert rows and rows[0]['name'] == 'ver_como_audit'


def test_admin_ve_el_boton_y_la_lista_incluye_herlinda():
    r = cliente(ADMIN).get('/ver-como')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Ver como asesora' in html
    assert 'value="herlinda"' in html
    assert 'Herlinda Guerrero' in html
    # También otras asesoras/líderes, nunca Mercadeo ni el admin.
    assert 'value="ana"' in html
    assert 'value="luz"' not in html
    assert 'value="mercadeo"' not in html
    assert 'value="inventario"' not in html
    # El botón está en Más, solo para el administrador.
    mas = cliente(ADMIN).get('/modulos').get_data(as_text=True)
    assert 'href="/ver-como"' in mas
    assert 'Ver como asesora' in mas


def test_luz_admin_tambien_puede():
    r = cliente('luz').get('/ver-como')
    assert r.status_code == 200
    assert 'value="herlinda"' in r.get_data(as_text=True)
    assert 'href="/ver-como"' in cliente('luz').get('/modulos').get_data(as_text=True)


def test_asesora_y_lider_no_pueden():
    for slug in ('herlinda', 'ana', 'antonio', 'miriam'):
        r = cliente(slug).get('/ver-como')
        assert r.status_code == 403, slug
        assert 'href="/ver-como"' not in cliente(slug).get('/modulos').get_data(as_text=True), slug
        r = cliente(slug).post('/ver-como', data={'csrf': csrf(slug), 'asesora': 'herlinda'})
        assert r.status_code == 403, slug
        with cliente(slug).session_transaction() as s:
            assert 'view_as' not in s, slug


def test_entrar_como_herlinda_y_volver():
    _limpia()
    c = cliente(ADMIN)
    r = c.post('/ver-como', data={'csrf': csrf(ADMIN), 'asesora': 'herlinda'})
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/portal')
    with c.session_transaction() as s:
        assert s['uid'] == ADMIN
        assert s['view_as'] == 'herlinda'
    r = c.get('/portal')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Viendo como Herlinda Guerrero' in html
    assert 'Volver a mi cuenta' in html
    assert 'action="/volver-a-mi-cuenta"' in html
    # La app se ve como asesora: menú de Herlinda, no el de Presidencia.
    assert 'Hola, Herlinda' in html
    assert 'href="/ver-como"' not in html
    assert 'href="/perfil"' in html
    filas = _audit()
    assert filas and filas[0]['admin_slug'] == ADMIN
    assert filas[0]['view_as'] == 'herlinda'
    assert filas[0]['action'] == 'entrar'

    r = c.post('/volver-a-mi-cuenta', data={'csrf': csrf(ADMIN)})
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/portal')
    with c.session_transaction() as s:
        assert s['uid'] == ADMIN
        assert 'view_as' not in s
    html = c.get('/portal').get_data(as_text=True)
    assert 'Viendo como' not in html
    assert 'Volver a mi cuenta' not in html
    assert 'href="/ver-como"' in c.get('/modulos').get_data(as_text=True)
    acciones = [f['action'] for f in _audit()]
    assert acciones == ['entrar', 'volver']
    assert all(f['admin_slug'] == ADMIN for f in _audit())


def test_view_as_plantado_en_asesora_se_ignora():
    c = cliente('herlinda')
    with c.session_transaction() as s:
        s['view_as'] = 'ana'
    r = c.get('/portal')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Viendo como' not in html
    assert 'Hola, Herlinda' in html
    with c.session_transaction() as s:
        assert 'view_as' not in s


def test_acciones_quedan_a_nombre_del_admin():
    _limpia()
    c = cliente(ADMIN)
    assert c.post('/ver-como', data={'csrf': csrf(ADMIN), 'asesora': 'herlinda'}).status_code == 302
    # Una escritura (p. ej. cambiar el perfil aparente) se audita con el admin real
    # y no le manda correo a la asesora.
    token = portal_lib.csrf_token(portal_lib.user_by_username('herlinda'))
    r = c.post('/perfil', data={'csrf': token, 'email': 'no-enviar@example.com', 'telefono': '',
                                'cedula': '', 'bio': '', 'zonas': ''},
               content_type='application/x-www-form-urlencoded')
    assert r.status_code in (200, 302)
    escrituras = [f for f in _audit() if f['action'] == 'escribir']
    assert escrituras, _audit()
    assert all(f['admin_slug'] == ADMIN and f['view_as'] == 'herlinda' for f in escrituras)
    assert all(f['path'] == '/perfil' for f in escrituras)
    omitidos = appmod.q(
        "SELECT estado, motivo, to_slug FROM email_outbox WHERE evento='perfil_actualizado'",
        fetch=True) or []
    assert omitidos, 'debía registrarse el correo omitido'
    assert all(o['estado'] == 'omitido' for o in omitidos)
    assert all('viendo como' in (o['motivo'] or '') for o in omitidos)


def test_sin_csrf_no_entra():
    c = cliente(ADMIN)
    r = c.post('/ver-como', data={'asesora': 'herlinda'})
    assert r.status_code == 400
    with c.session_transaction() as s:
        assert 'view_as' not in s


def test_no_se_puede_ver_como_admin_o_inventado():
    c = cliente(ADMIN)
    for slug in ('jose', 'luz', 'mercadeo', 'inventario', 'nadie'):
        r = c.post('/ver-como', data={'csrf': csrf(ADMIN), 'asesora': slug})
        assert r.status_code == 400, slug
        with c.session_transaction() as s:
            assert 'view_as' not in s, slug
