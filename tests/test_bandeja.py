"""Pruebas de la bandeja unificada, rol Mercadeo, Redes Sociales y Marketplace (SQLite local, sin red)."""
import hashlib, io, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmp = tempfile.mkdtemp()
os.environ['SQLITE_PATH'] = os.path.join(_tmp, 't.db')
os.environ.pop('DATABASE_URL', None)
os.environ['PANEL_PASSWORD'] = 'clave-jose'
os.environ['MERCADEO_PASSWORD'] = 'clave-mercadeo'
os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest(),
                                                   'luz': hashlib.sha256(b'clave-luz').hexdigest()})
for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX', 'META_PAGE_TOKEN', 'REDES_SOCIALES_JSON'):
    os.environ.pop(k, None)
os.chdir(_tmp)

import app as appmod  # noqa: E402
import bandeja, mensajes, marketplace, portal_lib, redes  # noqa: E402

CLI_ANA = '584121111111'
CLI_OTRO = '584122222222'


@pytest.fixture(autouse=True)
def entorno(monkeypatch):
    monkeypatch.setattr(mensajes, 'clientes_por_telefono', lambda: {
        CLI_ANA: [{'id': '1', 'nombre': 'Cliente Ana', 'asesor': 'ana', 'asignado_at': '2026-11-10', 'en_ejercicio': True}],
        CLI_OTRO: [{'id': '2', 'nombre': 'Cliente Otro', 'asesor': 'miriam', 'asignado_at': '2026-11-10', 'en_ejercicio': True}],
    })
    monkeypatch.delenv('META_WA_TOKEN', raising=False)
    monkeypatch.delenv('WA_PHONE_NUMBER_ID', raising=False)
    bandeja._rate.clear()
    appmod.q('DELETE FROM wa_events')
    yield


def entrante(wa, horas_atras=1, texto='Hola'):
    ts = (datetime.now(timezone.utc) - timedelta(hours=horas_atras)).isoformat()
    appmod.insert_event(kind='message', wamid=f'in-{wa}-{horas_atras}', wa_id=wa, name='Cliente', direction='inbound',
                        msg_type='text', text=texto, ts=ts, status='', source='meta', data={})


def cliente(slug):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s['uid'] = slug
    return c


def csrf(slug):
    return portal_lib.csrf_token(portal_lib.user_by_username(slug))


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ------------------------------------------------------------ ventana y filtro
def test_ventana_24h():
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    assert bandeja.ventana((now - timedelta(hours=23)).isoformat(), now)[0] is True
    assert bandeja.ventana((now - timedelta(hours=25)).isoformat(), now)[0] is False
    assert bandeja.ventana('', now)[0] is False


def test_revisar_texto():
    with pytest.raises(ValueError):
        bandeja.revisar_texto('   ')
    with pytest.raises(ValueError):
        bandeja.revisar_texto('escríbeme a wa.me/584121234567')
    with pytest.raises(ValueError):
        bandeja.revisar_texto('Llama a 0412-123.45.67', ['+58 412 1234567'])
    assert bandeja.revisar_texto('El alquiler es US$750 al mes.') == 'El alquiler es US$750 al mes.'


# ------------------------------------------------------------ permisos de la bandeja
def test_asesora_solo_ve_sus_conversaciones():
    entrante(CLI_ANA); entrante(CLI_OTRO)
    d = cliente('ana').get('/panel/data').get_json()
    assert {c['wa_id'] for c in d['conversations']} == {CLI_ANA}
    assert cliente('ana').get('/panel/data?c=' + CLI_OTRO).status_code == 403
    dj = cliente('jose').get('/panel/data').get_json()
    assert {c['wa_id'] for c in dj['conversations']} == {CLI_ANA, CLI_OTRO}


def test_responder_deshabilitado_sin_credenciales():
    entrante(CLI_ANA)
    d = cliente('ana').get('/panel/data?c=' + CLI_ANA).get_json()
    assert d['reply']['puede_enviar'] is False and 'no está activado' in d['reply']['motivo']
    r = cliente('ana').post('/panel/responder', data={'c': CLI_ANA, 'texto': 'hola', 'csrf': csrf('ana')},
                            headers={'Accept': 'application/json'})
    assert r.status_code == 409


def test_responder_envia_y_registra(monkeypatch):
    monkeypatch.setenv('META_WA_TOKEN', 'tok-prueba'); monkeypatch.setenv('WA_PHONE_NUMBER_ID', '123')
    enviados = []

    def fake_open(req, timeout=20):
        enviados.append(json.loads(req.data)); assert 'tok-prueba' in req.headers['Authorization']
        return FakeResp(json.dumps({'messages': [{'id': 'wamid.PRUEBA'}]}).encode())
    monkeypatch.setattr(bandeja.urllib.request, 'urlopen', fake_open)
    entrante(CLI_ANA)
    r = cliente('ana').post('/panel/responder', data={'c': CLI_ANA, 'texto': 'US$750 al mes', 'csrf': csrf('ana')},
                            headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.get_json()['ok']
    assert enviados[0]['to'] == CLI_ANA and enviados[0]['text']['body'] == 'US$750 al mes' and enviados[0]['type'] == 'text'
    row = appmod.q("SELECT source, wamid, data FROM wa_events WHERE kind='outbound'", fetch=True)[0]
    datos = row['data'] if isinstance(row['data'], str) else json.dumps(row['data'])  # SQLite: texto; Postgres: JSONB
    assert row['source'] == 'portal' and row['wamid'] == 'wamid.PRUEBA' and 'ana' in datos
    assert 'tok-prueba' not in r.get_data(as_text=True)


def test_responder_bloqueos(monkeypatch):
    monkeypatch.setenv('META_WA_TOKEN', 't'); monkeypatch.setenv('WA_PHONE_NUMBER_ID', '1')
    monkeypatch.setattr(bandeja.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('no debe enviar'))
    entrante(CLI_ANA); entrante(CLI_OTRO)
    h = {'Accept': 'application/json'}
    # sin CSRF
    assert cliente('ana').post('/panel/responder', data={'c': CLI_ANA, 'texto': 'x'}, headers=h).status_code == 400
    # conversación de otra asesora
    assert cliente('ana').post('/panel/responder', data={'c': CLI_OTRO, 'texto': 'x', 'csrf': csrf('ana')}, headers=h).status_code == 403
    # fuera de 24 h
    appmod.q('DELETE FROM wa_events'); entrante(CLI_ANA, horas_atras=30)
    assert cliente('ana').post('/panel/responder', data={'c': CLI_ANA, 'texto': 'x', 'csrf': csrf('ana')}, headers=h).status_code == 409
    # contacto que nunca escribió
    assert cliente('jose').post('/panel/responder', data={'c': '584129999999', 'texto': 'x', 'csrf': csrf('jose')}, headers=h).status_code == 409
    # sin sesión
    assert appmod.app.test_client().post('/panel/responder', data={'c': CLI_ANA, 'texto': 'x'}, headers=h).status_code == 401


def test_social_apagado_por_defecto():
    assert bandeja.social_activo() is False
    assert cliente('jose').get('/panel/data?c=ig:abc').status_code == 403


def test_social_solo_admin(monkeypatch):
    monkeypatch.setenv('META_SOCIAL_INBOX', '1'); monkeypatch.setenv('META_PAGE_TOKEN', 'pt')
    bandeja._cache.clear()

    def fake_open(req, timeout=20):
        url = req.full_url
        assert req.get_method() == 'GET'
        if '/conversations' in url:
            return FakeResp(json.dumps({'data': [{'id': 'C1', 'updated_time': '2026-11-16T15:00:00+0000',
                'participants': {'data': [{'id': '552585004598339', 'name': 'For Rent'}, {'id': '9', 'name': 'Pepa'}]},
                'messages': {'data': [{'message': 'Hola', 'from': {'id': '9'}, 'created_time': '2026-11-16T15:00:00+0000'}]}}]}).encode())
        return FakeResp(json.dumps({'data': [{'message': 'Hola', 'from': {'id': '9', 'name': 'Pepa'}, 'created_time': '2026-11-16T15:00:00+0000'}]}).encode())
    monkeypatch.setattr(bandeja.urllib.request, 'urlopen', fake_open)
    d = cliente('jose').get('/panel/data').get_json()
    assert any(c['wa_id'] == 'fb:C1' and c['canal'] == 'facebook' for c in d['conversations'])
    dt = cliente('jose').get('/panel/data?c=fb:C1').get_json()
    assert dt['messages'][0]['text'] == 'Hola' and dt['reply']['puede_enviar'] is False
    assert all(not c['wa_id'].startswith(('ig:', 'fb:')) for c in cliente('ana').get('/panel/data').get_json()['conversations'])
    assert cliente('ana').get('/panel/data?c=fb:C1').status_code == 403
    assert 'pt' not in json.dumps(d)


# ------------------------------------------------------------ rol Mercadeo
def test_login_mercadeo():
    assert portal_lib.identify_user('mercadeo', 'clave-mercadeo')['role'] == 'mercadeo'
    assert portal_lib.identify_user('mercadeo', 'mala') is None


def test_mercadeo_solo_sus_secciones():
    c = cliente('mercadeo')
    assert c.get('/mercadeo').status_code == 200
    assert c.get('/mercadeo/planificador').status_code == 200
    assert c.get('/').status_code == 302 and c.get('/').headers['Location'].endswith('/mercadeo')
    for ruta in ('/panel', '/panel/data', '/mis-clientes', '/inventario', '/cierres', '/clientes-propios', '/admin/correos'):
        assert c.get(ruta).status_code == 403, ruta
    assert c.post('/mercadeo/marketplace', data={'key': 'x', 'csrf': csrf('mercadeo')}).status_code == 403


def test_planificador_permisos():
    assert cliente('jose').get('/mercadeo/planificador').status_code == 200
    assert cliente('ana').get('/mercadeo/planificador').status_code == 403


def test_asesora_sigue_sin_editar_enlaces():
    assert cliente('ana').post('/mercadeo/link', data={'csrf': csrf('ana')}).status_code == 403
    assert cliente('ana').post('/mercadeo/marketplace', data={'csrf': csrf('ana')}).status_code == 403


# ------------------------------------------------------------ Redes Sociales y Marketplace
def test_redes_solo_enlaces_validos(monkeypatch):
    rs = {r['key']: r for r in redes.redes()}
    assert list(rs) == ['instagram', 'facebook', 'tiktok', 'youtube', 'linkedin']
    assert rs['instagram']['url'] == 'https://www.instagram.com/forrent.grupoinmobiliario'
    assert rs['linkedin']['visible'] is False
    monkeypatch.setenv('REDES_SOCIALES_JSON', json.dumps({'redes': {'instagram': 'https://evil.example/x'}}))
    assert {r['key']: r for r in redes.redes()}['instagram']['visible'] is False


def test_pagina_mercadeo_secciones():
    html = cliente('jose').get('/mercadeo').get_data(as_text=True)
    assert 'Redes Sociales' in html and 'Portales Inmobiliarios' in html and 'Facebook Marketplace' in html


def test_marketplace_url_valida():
    ok, e = marketplace.url_valida('https://m.facebook.com/marketplace/item/123456789/?ref=x&tracking=y')
    assert ok == 'https://www.facebook.com/marketplace/item/123456789/' and e is None
    for mala in ('http://www.facebook.com/marketplace/item/1', 'https://facebook.com.evil.com/marketplace/item/1',
                 'https://www.facebook.com/forrent', 'https://www.facebook.com/marketplace/', '', 'javascript:alert(1)'):
        assert marketplace.url_valida(mala)[0] is None, mala


def test_marketplace_guardar_bitacora():
    q = appmod.q
    q('DELETE FROM crm_marketplace_links'); q('DELETE FROM crm_marketplace_links_log')
    assert marketplace.guardar(q, key='c:1', codigo='1', url='https://www.facebook.com/marketplace/item/1/', por='luz', is_pg=False) == 'alta'
    assert marketplace.guardar(q, key='c:1', codigo='1', url='https://www.facebook.com/marketplace/item/1/', por='luz', is_pg=False) == 'sin_cambio'
    assert marketplace.guardar(q, key='c:1', codigo='1', url='https://www.facebook.com/marketplace/item/2/', por='jose', is_pg=False) == 'reemplazo'
    assert marketplace.quitar(q, key='c:1', por='luz') is True
    h = marketplace.historial(q, 'c:1')
    assert [x['accion'] for x in h] == ['baja', 'reemplazo', 'alta'] and [x['por'] for x in h] == ['luz', 'jose', 'luz']
    assert all(x['at'] for x in h)


def test_luz_es_admin():
    u = portal_lib.user_by_username('luz')
    assert u['role'] == 'admin'
    assert portal_lib.user_by_username('ana')['role'] != 'admin'


def test_marketplace_ruta_luz_y_jose():
    appmod.q('DELETE FROM crm_marketplace_links'); appmod.q('DELETE FROM crm_marketplace_links_log')
    with appmod.app.test_request_context():
        _, items, _ = appmod._mercadeo_items(portal_lib.user_by_username('jose'))
    if not items:
        pytest.skip('sin inventario local')
    key = appmod.portales.key_de(items[0])
    r = cliente('luz').post('/mercadeo/marketplace', data={'key': key, 'url': 'https://www.facebook.com/marketplace/item/42/', 'csrf': csrf('luz')})
    assert r.status_code == 302 and 'ok=mp' in r.headers['Location']
    r = cliente('luz').post('/mercadeo/marketplace', data={'key': key, 'url': 'https://www.facebook.com/forrent', 'csrf': csrf('luz')})
    assert r.status_code == 400
    r = cliente('jose').post('/mercadeo/marketplace', data={'key': key, 'accion': 'quitar', 'csrf': csrf('jose')})
    assert r.status_code == 302
    assert [h['por'] for h in marketplace.historial(appmod.q, key)] == ['jose', 'luz']
    assert cliente('luz').post('/mercadeo/marketplace', data={'key': key, 'url': 'https://www.facebook.com/marketplace/item/1/'}).status_code == 400
