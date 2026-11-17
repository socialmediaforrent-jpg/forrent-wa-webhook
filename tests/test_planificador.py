"""Planificador propio (sin GHL): permisos en el servidor, importación única del export GHL y CRUD.
SQLite local, sin red. Todo lo que hay aquí son DATOS DE PRUEBA."""
import hashlib, io, json, os, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if 'app' not in sys.modules:
    _tmp = tempfile.mkdtemp()
    os.environ['SQLITE_PATH'] = os.path.join(_tmp, 't.db')
    os.environ.pop('DATABASE_URL', None)
    os.environ['PANEL_PASSWORD'] = 'clave-jose'
    os.environ['MERCADEO_PASSWORD'] = 'clave-mercadeo'
    os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest(),
                                                       'luz': hashlib.sha256(b'clave-luz').hexdigest()})
    os.chdir(_tmp)
os.environ['EMAIL_MODE'] = 'log'

import app as appmod  # noqa: E402
import planificador as planif, portal_lib  # noqa: E402

PRUEBA = {'actualizado': '2026-11-16T17:10:37+00:00', 'fuente': 'PRUEBA', 'posts': [
    {'post_id': 'prueba-1', 'fecha_hora_toronto': '2026-11-17 09:00', 'texto': 'PRUEBA uno',
     'cuentas': 'facebook:For Rent Caracas | instagram:forrent.caracas', 'media_urls': 'https://x.test/a.mp4'},
    {'post_id': 'prueba-2', 'fecha_hora_toronto': '2026-11-18 14:00', 'texto': 'PRUEBA <b>dos</b>',
     'cuentas': 'tiktok:ForRent.GrupoInmobiliario', 'media_urls': 'javascript:alert(1)'},
]}
FB, IG, TT = 'facebook:For Rent Caracas', 'instagram:forrent.caracas', 'tiktok:ForRent.GrupoInmobiliario'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def cliente(slug):
    c = appmod.app.test_client()
    if slug:
        with c.session_transaction() as s:
            s['uid'] = slug
    return c


def csrf(slug):
    return portal_lib.csrf_token(portal_lib.user_by_username(slug))


@pytest.fixture(autouse=True)
def datos(monkeypatch):
    real = appmod._portal_live
    snapshot = json.dumps(PRUEBA, sort_keys=True)
    monkeypatch.setattr(appmod, '_portal_live', lambda n: PRUEBA if n == 'planificador.json' else real(n))
    cliente('jose').get('/mercadeo/planificador')  # dispara la importación única
    yield
    assert json.dumps(PRUEBA, sort_keys=True) == snapshot  # el JSON de respaldo no se toca


def n_posts(extra=''):
    return appmod.q('SELECT COUNT(*) AS n FROM social_posts WHERE 1=1 ' + extra, fetch=True)[0]['n']


def guardar(slug='mercadeo', **kw):
    d = {'csrf': csrf(slug), 'texto': 'PRUEBA texto', 'fecha': '2027-01-15', 'hora': '10:30',
         'cuentas': [FB, IG], 'accion': 'programar'}
    d.update(kw)
    return cliente(slug).post('/mercadeo/planificador/guardar', data=d, content_type='multipart/form-data')


def ultimo():
    return appmod.q('SELECT * FROM social_posts ORDER BY id DESC LIMIT 1', fetch=True)[0]


# ------------------------------------------------------------ permisos (servidor)
@pytest.mark.parametrize('ruta', ['/mercadeo/planificador', '/mercadeo/planificador/nuevo', '/mercadeo/planificador/1'])
def test_asesora_no_ve_planificador(ruta):
    r = cliente('ana').get(ruta)
    assert r.status_code == 403
    assert 'PRUEBA' not in r.get_data(as_text=True)


def test_asesora_no_puede_escribir():
    antes = n_posts()
    assert guardar('ana').status_code == 403
    for acc in ('duplicar', 'borrar'):
        assert cliente('ana').post(f'/mercadeo/planificador/1/{acc}', data={'csrf': csrf('ana')}).status_code == 403
    assert cliente('ana').get('/mercadeo/planificador/media/1').status_code == 403
    assert n_posts() == antes


def test_sin_sesion_va_al_login():
    r = cliente(None).get('/mercadeo/planificador')
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert cliente(None).post('/mercadeo/planificador/guardar', data={}).status_code in (302, 401)


def test_presidencia_y_mercadeo_si_lo_ven():
    for u in ('jose', 'mercadeo'):
        for ruta in ('/mercadeo/planificador', '/mercadeo/planificador/nuevo'):
            assert cliente(u).get(ruta).status_code == 200, (u, ruta)


def test_nav_de_asesora_sin_planificador():
    assert '/mercadeo/planificador' not in cliente('ana').get('/portal').get_data(as_text=True)


def test_solo_presidencia_aprueba():
    pid = ultimo()['id']
    assert cliente('mercadeo').post(f'/mercadeo/planificador/{pid}/aprobacion', data={'csrf': csrf('mercadeo'), 'valor': 'Aprobado'}).status_code == 403
    r = cliente('jose').post(f'/mercadeo/planificador/{pid}/aprobacion', data={'csrf': csrf('jose'), 'valor': 'Aprobado'})
    assert r.status_code == 302
    f = appmod.q('SELECT aprobacion, aprobador FROM social_posts WHERE id=%s', (pid,), fetch=True)[0]
    assert f['aprobacion'] == 'Aprobado' and f['aprobador'] == 'jose'


# ------------------------------------------------------------ importación única
def test_importacion_unica_y_sin_inventar():
    assert n_posts("AND origen='ghl_import'") == 2
    assert {c['id'] for c in planif.cuentas(appmod.q)} >= {FB, IG, TT}
    assert planif.importar_ghl_una_vez(appmod.q, PRUEBA) == 0  # no se repite
    html = cliente('jose').get('/mercadeo/planificador').get_data(as_text=True)
    assert '<b>dos</b>' not in html and 'javascript:alert' not in html
    assert 'For Rent Grupo Inmobiliario C.A.' not in html
    assert n_posts("AND origen='ghl_import'") == 2


# ------------------------------------------------------------ CRUD
def test_crear_programado_y_borrador():
    antes = n_posts()
    r = guardar()
    assert r.status_code == 302 and 'ok=creado' in r.headers['Location']
    f = ultimo()
    assert f['estado'] == 'Programado' and f['programado_local'] == '2027-01-15 10:30'
    assert f['programado_utc'].startswith('2027-01-15T15:30') and f['creado_por'] == 'mercadeo'
    assert json.loads(f['cuentas']) == [FB, IG]
    assert guardar(accion='borrador', fecha='', cuentas=[]).status_code == 302
    assert ultimo()['estado'] == 'Borrador'
    assert n_posts() == antes + 2


def test_validaciones():
    antes = n_posts()
    assert guardar(cuentas=[]).status_code == 400                      # programar sin cuentas
    assert guardar(fecha='').status_code == 400                        # programar sin fecha
    assert guardar(fecha='2020-01-01').status_code == 400              # fecha pasada
    assert guardar(cuentas=['facebook:Cuenta Inventada']).status_code == 400  # cuenta que no existe
    assert guardar(enlaces='http://inseguro.test/a.jpg').status_code == 400
    assert guardar(csrf='malo').status_code == 400
    assert n_posts() == antes


def test_doble_toque_guarda_una_vez():
    antes = n_posts()
    assert guardar(idem='tok-prueba-doble').status_code == 302
    assert guardar(idem='tok-prueba-doble').status_code == 302
    assert n_posts() == antes + 1


def test_subir_imagen_y_servirla():
    r = guardar(archivos=(io.BytesIO(PNG), 'prueba.png'))
    assert r.status_code == 302
    media = json.loads(ultimo()['media'])
    assert media and media[0].startswith('media:')
    mid = int(media[0][6:])
    m = cliente('mercadeo').get(f'/mercadeo/planificador/media/{mid}')
    assert m.status_code == 200 and m.mimetype == 'image/png' and m.data == PNG
    assert guardar(archivos=(io.BytesIO(b'no soy imagen'), 'x.exe')).status_code == 400


def test_editar_duplicar_borrar():
    guardar(texto='PRUEBA original')
    pid = ultimo()['id']
    assert cliente('jose').get(f'/mercadeo/planificador/{pid}').status_code == 200
    assert cliente('jose').get(f'/mercadeo/planificador/{pid}?ver=1').status_code == 200
    r = guardar('jose', id=str(pid), texto='PRUEBA editada', hora='11:00')
    assert r.status_code == 302 and 'ok=editado' in r.headers['Location']
    f = appmod.q('SELECT * FROM social_posts WHERE id=%s', (pid,), fetch=True)[0]
    assert f['texto'] == 'PRUEBA editada' and f['programado_local'] == '2027-01-15 11:00' and f['actualizado_por'] == 'jose'
    r = cliente('mercadeo').post(f'/mercadeo/planificador/{pid}/duplicar', data={'csrf': csrf('mercadeo')})
    assert r.status_code == 302
    d = ultimo()
    assert d['id'] != pid and d['estado'] == 'Borrador' and d['texto'] == 'PRUEBA editada'
    r = cliente('mercadeo').post(f'/mercadeo/planificador/{pid}/borrar', data={'csrf': csrf('mercadeo')})
    assert r.status_code == 302
    f = appmod.q('SELECT deleted_at, deleted_by FROM social_posts WHERE id=%s', (pid,), fetch=True)[0]
    assert f['deleted_at'] and f['deleted_by'] == 'mercadeo'           # borrado lógico: la fila sigue
    assert pid not in [p['id'] for p in planif.listar(appmod.q)]
    assert cliente('jose').get(f'/mercadeo/planificador/{pid}').status_code == 404
    assert cliente('mercadeo').post(f'/mercadeo/planificador/{pid}/borrar', data={'csrf': csrf('mercadeo')}).status_code == 404


def test_sin_codigo_de_publicacion_en_redes():
    src = open(os.path.join(ROOT, 'planificador.py')).read() + open(os.path.join(ROOT, 'app.py')).read()
    for s in ('graph.facebook.com/v', '/media_publish', 'open.tiktokapis.com', 'api.pinterest.com'):
        assert s not in src


# ------------------------------------------------------------ publicador (simulación)
def test_publicador_simulacion_nunca_toma_ghl_ni_cuentas_apagadas():
    import publicador
    from datetime import datetime, timezone
    q = appmod.q
    q("UPDATE social_posts SET programado_utc='2020-01-01T00:00:00+00:00' WHERE origen='ghl_import'")  # vencidas a propósito
    guardar(texto='PRUEBA vencida')
    pid = ultimo()['id']
    q("UPDATE social_posts SET programado_utc='2020-01-01T00:00:00+00:00' WHERE id=%s", (pid,))
    el, apag, venc = publicador.pendientes(q)
    assert el == [] and venc >= 1                                            # todas las cuentas nacen apagadas
    assert all(r['origen'] == 'portal' for r, _ in el)
    q("UPDATE social_accounts SET publicar_auto=1 WHERE clave=%s", (FB,))
    try:
        el, _, _ = publicador.pendientes(q)
        assert pid in [r['id'] for r, _ in el] and all(r['origen'] == 'portal' for r, _ in el)
        assert all(cs == [FB] for _, cs in el)
    finally:
        q("UPDATE social_accounts SET publicar_auto=0")
    antes = q('SELECT COUNT(*) AS n FROM social_posts WHERE estado=%s', ('Programado',), fetch=True)[0]['n']
    assert publicador.correr(q)['modo'] == 'simulacion'
    assert q('SELECT COUNT(*) AS n FROM social_posts WHERE estado=%s', ('Programado',), fetch=True)[0]['n'] == antes
    src = open(os.path.join(ROOT, 'publicador.py')).read()
    assert 'urllib' not in src and 'requests' not in src and 'http' not in src.replace('https://', '')
