"""Planificador de redes sociales (estilo GHL): permisos en el servidor y datos solo de planificador.json.
SQLite local, sin red. Los posts de aquí son DATOS DE PRUEBA."""
import hashlib, json, os, sys, tempfile

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

import app as appmod  # noqa: E402

PRUEBA = {'actualizado': '2026-11-16T17:10:37+00:00', 'fuente': 'PRUEBA', 'posts': [
    {'post_id': 'p1', 'fecha_hora_toronto': '2026-11-17 09:00', 'texto': 'PRUEBA uno',
     'cuentas': 'facebook:For Rent Caracas | instagram:forrent.caracas', 'media_urls': 'https://x.test/a.mp4'},
    {'post_id': 'p2', 'fecha_hora_toronto': '2026-11-18 14:00', 'texto': 'PRUEBA <b>dos</b>',
     'cuentas': 'tiktok:ForRent.GrupoInmobiliario', 'media_urls': 'javascript:alert(1)'},
]}


def cliente(slug):
    c = appmod.app.test_client()
    if slug:
        with c.session_transaction() as s:
            s['uid'] = slug
    return c


@pytest.fixture(autouse=True)
def datos(monkeypatch):
    real = appmod._portal_live
    monkeypatch.setattr(appmod, '_portal_live', lambda n: PRUEBA if n == 'planificador.json' else real(n))


def test_asesora_no_ve_planificador():
    r = cliente('ana').get('/mercadeo/planificador')
    assert r.status_code == 403
    assert 'PRUEBA' not in r.get_data(as_text=True)


def test_sin_sesion_va_al_login():
    r = cliente(None).get('/mercadeo/planificador')
    assert r.status_code == 302 and '/login' in r.headers['Location']


def test_presidencia_y_mercadeo_si_lo_ven():
    for u in ('jose', 'mercadeo'):
        r = cliente(u).get('/mercadeo/planificador')
        assert r.status_code == 200, u
        assert 'Planificador de redes sociales' in r.get_data(as_text=True)


def test_nav_de_asesora_sin_planificador():
    html = cliente('ana').get('/portal').get_data(as_text=True)
    assert '/mercadeo/planificador' not in html


def test_cuentas_salen_de_los_datos_y_nada_inventado():
    html = cliente('jose').get('/mercadeo/planificador').get_data(as_text=True)
    for n in ('For Rent Caracas', 'forrent.caracas', 'ForRent.GrupoInmobiliario'):
        assert n in html
    assert 'For Rent Grupo Inmobiliario C.A.' not in html  # no está en los datos de prueba
    assert html.count('data-acc="') == 3
    assert 'Seleccione la(s) cuenta(s)' in html and 'Filtros' in html and 'Vista semanal' in html


def test_texto_escapado_y_media_solo_https():
    html = cliente('jose').get('/mercadeo/planificador').get_data(as_text=True)
    assert '<b>dos</b>' not in html
    assert 'javascript:alert' not in html
    data = json.loads(html.split('id="pz-data">', 1)[1].split('</script>', 1)[0])
    assert [p['estado'] for p in data['posts']] == ['Programado', 'Programado']
    assert [p['tipo'] for p in data['posts']] == ['Editor de publicaciones'] * 2
    assert data['posts'][1]['media'] == []
