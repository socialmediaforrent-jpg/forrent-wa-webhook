"""Carga única (PRUEBA): formulario de 4 pasos, fotos en BD, cola de Inventario, duplicados, Wasi en DRY-RUN,
XML Agentiz, kits y borrador del planificador. SQLite local, sin red: cualquier intento de red hace fallar el test."""
import hashlib, io, json, os, re, socket, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if 'app' not in sys.modules:
    _tmp = tempfile.mkdtemp()
    os.environ['SQLITE_PATH'] = os.path.join(_tmp, 't.db')
    os.environ.pop('DATABASE_URL', None)
    os.environ['PANEL_PASSWORD'] = 'clave-jose'
    os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest()})
    for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX', 'META_PAGE_TOKEN'):
        os.environ.pop(k, None)
    os.chdir(_tmp)

import app as appmod  # noqa: E402
import carga, portal_lib  # noqa: E402

JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 200
INV = {'actualizado': '2026-11-16', 'grupos': {
    'propio': [{'titulo': 'Apartamento en alquiler Los Palos Grandes 3 hab', 'operacion': 'Alquiler', 'tipo': 'Apartamento',
                'zona': 'Los Palos Grandes', 'precio': 'US$ 1.000', 'codigo': '111', 'origen': 'propio'}],
    'alianza': [], 'red': []}}
P1 = {'operacion': 'Alquiler', 'tipo': 'Apartamento', 'titulo': 'Apartamento amoblado 3 hab Los Palos Grandes',
      'precio': '950', 'condominio': '80', 'estado_fisico': 'Usado', 'origen': 'propia'}
P2 = {'municipio': 'Chacao', 'zona': 'Los Palos Grandes', 'direccion': 'Av. Secreta 123, Torre X, piso 9', 'lat': '10.5', 'lng': '-66.84'}
P3 = {'habitaciones': '3', 'banos': '2', 'estacionamientos': '1', 'area_construida': '110', 'mascotas': 'si',
      'descripcion': 'Apartamento luminoso, amoblado, cerca del metro y de comercios. <b>Listo</b> para mudarse.'}
P4 = {'video': 'https://youtu.be/abc123', 'propietario_nombre': 'Pedro Dueño', 'propietario_tel': '0414-555-0000', 'nota_interna': 'llave en conserjería'}


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    inv = lambda: json.loads(json.dumps(INV))
    monkeypatch.setattr(appmod, '_inventario_raw', inv)
    monkeypatch.setattr(appmod, '_inventario', lambda: appmod._inventario_sin_red(inv()))
    monkeypatch.setenv('INVENTARIO_PASSWORD_SHA256', hashlib.sha256(b'clave-inv').hexdigest())
    monkeypatch.delenv('WASI_WRITE_MODE', raising=False)
    monkeypatch.delenv('AGENTIZ_FEED_TOKEN', raising=False)

    def sin_red(*a, **k):
        raise AssertionError('intento de red en test')
    monkeypatch.setattr(socket, 'create_connection', sin_red)
    appmod.q('DELETE FROM carga_fotos'); appmod.q('DELETE FROM carga_log'); appmod.q('DELETE FROM carga_inmuebles')
    yield


def cliente(slug):
    c = appmod.app.test_client()
    if slug:
        with c.session_transaction() as s:
            s['uid'] = slug
    return c


def csrf(slug):
    return portal_lib.csrf_token(portal_lib.user_by_username(slug))


def nueva(slug='antonio'):
    r = cliente(slug).post('/mis-inmuebles/nuevo', data={'csrf': csrf(slug)})
    assert r.status_code == 302
    return int(re.search(r'/mis-inmuebles/(\d+)/paso/1', r.headers['Location']).group(1))


def completa(slug='antonio', n_fotos=6, p1=None):
    cid, c = nueva(slug), cliente(slug)
    for n, d in ((1, p1 or P1), (2, P2), (3, P3), (4, P4)):
        assert c.post(f'/mis-inmuebles/{cid}/paso/{n}', data=d | {'csrf': csrf(slug), 'ir': 'siguiente'}).status_code == 302
    for i in range(n_fotos):
        r = c.post(f'/mis-inmuebles/{cid}/fotos', data={'csrf': csrf(slug), 'fotos': (io.BytesIO(JPG + bytes([i]) * 10), f'f{i}.jpg')},
                   content_type='multipart/form-data')
        assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return cid


def enviar(cid, slug='antonio'):
    return cliente(slug).post(f'/mis-inmuebles/{cid}/enviar', data={'csrf': csrf(slug)})


def test_permisos():
    assert cliente(None).get('/mis-inmuebles').status_code in (302, 401)
    assert cliente('inventario').get('/mis-inmuebles').status_code == 403
    assert cliente('antonio').get('/inventario/cola').status_code == 403
    assert cliente('ana').get('/inventario/cola').status_code == 403
    assert cliente('inventario').get('/inventario/cola').status_code == 200
    assert cliente('jose').get('/inventario/cola').status_code == 200
    cid = nueva('antonio')
    assert cliente('herlinda').get(f'/mis-inmuebles/{cid}/paso/1').status_code == 404   # ficha ajena
    assert cliente('herlinda').post(f'/mis-inmuebles/{cid}/paso/1', data=P1 | {'csrf': csrf('herlinda')}).status_code == 404
    assert cliente('antonio').post(f'/mis-inmuebles/{cid}/paso/1', data=P1 | {'csrf': 'malo'}).status_code == 400


def test_pasos_normalizan_y_privado_separado():
    cid = completa()
    c = carga.una(appmod.q, cid)
    assert c['datos']['titulo'].startswith('Apartamento amoblado') and c['datos']['precio'] == 950
    assert '<b>' not in c['datos']['descripcion']
    assert 'direccion' not in c['datos'] and c['privado']['direccion'].startswith('Av. Secreta')
    assert c['privado']['propietario_tel'] == '04145550000'
    h = cliente('antonio').get(f'/mis-inmuebles/{cid}/paso/2').get_data(as_text=True)
    assert 'Av. Secreta 123' in h and 'Ubicación' in h


def test_fotos_limites_tipo_y_duplicado():
    cid, c = nueva(), cliente('antonio')
    def sube(b, n='x.jpg'):
        return c.post(f'/mis-inmuebles/{cid}/fotos', data={'csrf': csrf('antonio'), 'fotos': (io.BytesIO(b), n)}, content_type='multipart/form-data')
    assert sube(b'GIF89a' + b'0' * 50, 'x.gif').status_code == 400
    assert sube(b'<svg onload=alert(1)>', 'x.jpg').status_code == 400
    assert sube(JPG).status_code == 302
    assert sube(JPG).status_code == 400            # misma foto dos veces
    assert sube(b'\xff\xd8\xff' + b'1' * (carga.FOTO_MAX_BYTES + 5)).status_code == 400
    assert len(carga.fotos(appmod.q, cid)) == 1
    fid = carga.fotos(appmod.q, cid)[0]['id']
    r = c.get(f'/carga/foto/{fid}')
    assert r.status_code == 200 and r.headers['Content-Type'] == 'image/jpeg' and r.headers['X-Content-Type-Options'] == 'nosniff'
    assert cliente('herlinda').get(f'/carga/foto/{fid}').status_code == 404
    assert cliente('inventario').get(f'/carga/foto/{fid}').status_code == 200
    assert c.post(f'/mis-inmuebles/{cid}/fotos/{fid}/quitar', data={'csrf': csrf('antonio')}).status_code == 302
    assert carga.fotos(appmod.q, cid) == []
    assert appmod.q('SELECT COUNT(*) AS n FROM carga_fotos', fetch=True)[0]['n'] == 1   # se oculta, no se borra


def test_no_envia_incompleta_y_bloquea_edicion():
    cid = completa(n_fotos=5)
    r = enviar(cid)
    assert r.status_code == 400 and 'Fotos (mínimo 6' in r.get_data(as_text=True)
    cid = completa()
    assert enviar(cid).status_code == 302 and carga.una(appmod.q, cid)['estado'] == 'enviada'
    assert cliente('antonio').post(f'/mis-inmuebles/{cid}/paso/1', data=P1 | {'csrf': csrf('antonio'), 'titulo': 'cambio'}).status_code == 409


def test_cola_duplicados_y_aprobar_dry_run():
    cid = completa(); enviar(cid)
    c2 = completa('herlinda'); enviar(c2, 'herlinda')
    inv = cliente('inventario')
    h = inv.get(f'/inventario/cola/{cid}').get_data(as_text=True)
    assert 'Posibles duplicados (2)' in h and 'Inventario (propio)' in h and f'Carga #{c2}' in h
    assert 'Av. Secreta' in h            # Inventario sí ve lo privado
    assert inv.post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('inventario'), 'accion': 'rechazar'}).status_code == 400  # sin nota
    r = inv.post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('inventario'), 'accion': 'aprobar', 'portales': ['75', '43', '999']})
    assert r.status_code == 302 and 'ok=aprobada' in r.headers['Location']
    c = carga.una(appmod.q, cid)
    assert c['estado'] == 'aprobada' and c['wasi_modo'] == 'dry-run' and c['wasi_id'] in (None, '')
    body = c['wasi_payload']['pasos'][0]['body']
    assert c['wasi_payload']['pasos'][0]['uri'] == 'https://api.wasi.co/v1/property/add'
    assert body['portals'] == [75, 43] and body['id_property_type'] == 2 and body['rent_price'] == 950 and body['id_currency'] == 3
    assert body['id_publish_on_map'] == 2 and body['reference'] == f'PORTAL-{cid}'
    assert 'Av. Secreta' not in body['observations'] and 'Av. Secreta' in body['comment'] and 'Pedro' in body['comment']
    assert sum(1 for p in c['wasi_payload']['pasos'] if 'upload-image' in p['uri']) == 6
    assert any('Mercado Libre' in a for a in c['wasi_payload']['avisos'])
    txt = json.dumps(c['wasi_payload'])
    assert 'token' not in txt.lower() and 'id_company' not in txt
    # borrador en el planificador propio
    post = appmod.q('SELECT * FROM social_posts WHERE id=%s', (c['social_post_id'],), fetch=True)[0]
    assert post['estado'] == 'Borrador' and 'Apartamento amoblado' in post['texto'] and 'Av. Secreta' not in post['texto']
    # segunda decisión no cambia nada
    assert inv.post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('inventario'), 'accion': 'rechazar', 'nota': 'x'}).status_code == 409


def test_devolver_permite_corregir():
    cid = completa(); enviar(cid)
    cliente('jose').post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('jose'), 'accion': 'devolver', 'nota': 'Faltan fotos de la cocina'})
    c = carga.una(appmod.q, cid)
    assert c['estado'] == 'devuelta'
    assert 'Faltan fotos de la cocina' in cliente('antonio').get('/mis-inmuebles').get_data(as_text=True)
    assert cliente('antonio').post(f'/mis-inmuebles/{cid}/paso/1', data=P1 | {'csrf': csrf('antonio')}).status_code == 302
    assert enviar(cid).status_code == 302 and carga.una(appmod.q, cid)['estado'] == 'enviada'


def test_modo_live_no_escribe(monkeypatch):
    monkeypatch.setenv('WASI_WRITE_MODE', 'live')
    cid = completa(); enviar(cid)
    cliente('inventario').post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('inventario'), 'accion': 'aprobar'})
    c = carga.una(appmod.q, cid)
    assert c['wasi_modo'] == 'live' and c['wasi_resultado']['ok'] is False and not c['wasi_id']


def test_agentiz_y_kits_sin_privados(monkeypatch):
    a = completa(); enviar(a)
    b = completa('herlinda', p1=P1 | {'origen': 'alianza', 'aliado': 'Inmobiliaria X', 'titulo': 'Oficina distinta en Chacao centro'}); enviar(b, 'herlinda')
    for cid in (a, b):
        cliente('inventario').post(f'/inventario/cola/{cid}/decidir', data={'csrf': csrf('inventario'), 'accion': 'aprobar'})
    x = cliente('inventario').get('/inventario/agentiz.xml').get_data(as_text=True)
    assert f'<external_id>FR-{a}</external_id>' in x and f'FR-{b}<' not in x      # alianza no va
    assert '<rental><per_month>950</per_month></rental>' in x and '<area_2>Chacao</area_2>' in x
    assert 'Secreta' not in x and 'Pedro' not in x and '<b>' not in x
    import xml.etree.ElementTree as ET
    ET.fromstring(x.encode())
    assert cliente(None).get('/feeds/agentiz.xml').status_code == 404
    monkeypatch.setenv('AGENTIZ_FEED_TOKEN', 'tok-prueba')
    assert cliente(None).get('/feeds/agentiz.xml?t=malo').status_code == 404
    assert cliente(None).get('/feeds/agentiz.xml?t=tok-prueba').status_code == 200
    k = cliente('inventario').get(f'/inventario/cola/{a}/kit').get_data(as_text=True)
    assert 'Encuadras' in k and 'Rex' in k and 'Secreta' not in k and 'Pedro' not in k and 'conserjería' not in k
    kb = cliente('inventario').get(f'/inventario/cola/{b}/kit').get_data(as_text=True)
    assert '>Rex<' not in kb                                                      # Rex solo propias
    assert cliente('antonio').get(f'/inventario/cola/{a}/kit').status_code == 403
