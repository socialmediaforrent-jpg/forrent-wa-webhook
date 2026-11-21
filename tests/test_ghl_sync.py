"""Sincronización GHL, canales, tarjetas de la lista y pull-to-refresh (sin red)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from test_bandeja import appmod, bandeja, cliente, csrf, entorno, mensajes  # noqa: F401
import ghl_sync


NOW = datetime(2026, 11, 21, 19, 0, tzinfo=timezone.utc)


def _msg(*, mid='m1', typ='TYPE_WHATSAPP', direction='inbound', body='Hola',
         contact='cid1', conv='cv1', alt='', frm='584121111111', to='584242294196',
         date=None, atts=None):
    return {
        'id': mid, 'messageType': typ, 'direction': direction, 'body': body,
        'contactId': contact, 'conversationId': conv, 'altId': alt,
        'from': frm, 'to': to, 'dateAdded': date or '2026-11-21T18:00:00.000Z',
        'status': 'delivered', 'attachments': atts or [],
    }


def test_thread_id_separa_canales_y_une_whatsapp_por_telefono():
    wa = _msg()
    assert ghl_sync.thread_id('whatsapp', wa, {'contactId': 'cid1'}) == '584121111111'
    ig = _msg(typ='TYPE_INSTAGRAM', frm='igsid-largo', to='pageid', contact='cidIG')
    assert ghl_sync.thread_id('instagram', ig, {'contactId': 'cidIG'}) == 'ig:cidIG'
    fb = _msg(typ='TYPE_FACEBOOK', contact='cidFB')
    assert ghl_sync.thread_id('facebook', fb, {'contactId': 'cidFB'}) == 'fb:cidFB'
    em = _msg(typ='TYPE_EMAIL', frm='', to='')
    assert ghl_sync.thread_id('email', em, {'contactId': 'cidE', 'contactName': 'demo@forrent.test'}) == 'email:demo@forrent.test'
    sms = _msg(typ='TYPE_SMS', frm='584123333333', to='584242294196')
    assert ghl_sync.thread_id('sms', sms, {'contactId': 'cidS'}) == 'sms:584123333333'


def test_fila_y_dedupe_por_id_ghl_y_altid_meta():
    conv = {'id': 'cv1', 'contactId': 'cid1', 'contactName': 'Carla Test', 'lastMessageType': 'TYPE_WHATSAPP'}
    fila = ghl_sync.fila_de_mensaje(_msg(mid='abc', alt='wamid.META1'), conv)
    assert fila['wamid'] == 'ghl:abc'
    assert fila['source'] == 'ghl_sync'
    assert fila['data']['channel'] == 'whatsapp'
    assert fila['data']['meta_wamid'] == 'wamid.META1'
    assert fila['kind'] == 'message'
    conocidos = {'ghl:abc'}
    assert ghl_sync.ya_existe(fila, conocidos) is True
    conocidos = {'wamid.META1'}
    assert ghl_sync.ya_existe(fila, conocidos) is True
    assert ghl_sync.ya_existe(ghl_sync.fila_de_mensaje(_msg(mid='nuevo', alt=''), conv), conocidos) is False


def test_aplica_filas_sin_duplicar():
    guardadas = []

    def insert_event(**kw):
        guardadas.append(kw)

    conv = {'id': 'cv', 'contactId': 'c', 'contactName': 'Carla Test'}
    a = ghl_sync.fila_de_mensaje(_msg(mid='n1', alt='wamid.X'), conv)
    b = ghl_sync.fila_de_mensaje(_msg(mid='n1', alt='wamid.X'), conv)  # mismo id GHL
    c = ghl_sync.fila_de_mensaje(_msg(mid='n2', alt='wamid.X'), conv)  # mismo Meta
    d = ghl_sync.fila_de_mensaje(_msg(mid='n3', alt='wamid.Y', body='Otro'), conv)
    ins, omit = ghl_sync.aplicar_filas([a, b, c, d], insert_event=insert_event, conocidos_iniciales=set())
    assert ins == 2 and omit == 2
    assert {x['wamid'] for x in guardadas} == {'ghl:n1', 'ghl:n3'}


def test_canal_de_no_confunde_ghl_import_con_instagram():
    row = {'source': 'ghl_import', 'wa_id': '584121111111', 'data': {'channel': 'whatsapp'}}
    assert mensajes.canal_de(row) == 'whatsapp'
    row2 = {'source': 'ghl_sync', 'wa_id': 'ig:cid', 'data': {'channel': 'instagram'}}
    assert mensajes.canal_de(row2) == 'instagram'
    row3 = {'source': 'ghl_import', 'wa_id': '58412', 'data': {}}
    assert mensajes.canal_de(row3) == 'whatsapp'


def _ev(wa, *, name, text, canal, hours=1, direction='inbound', source='ghl_sync', mid=None):
    ts = (NOW - timedelta(hours=hours)).isoformat()
    data = {'channel': canal, 'canal': canal, 'ghl_contact_id': 'cid-' + wa[:8]}
    appmod.insert_event(
        kind='message' if direction == 'inbound' else 'outbound',
        wamid=mid or ('ghl:' + wa + str(hours)),
        wa_id=wa, name=name, direction=direction, msg_type='text', text=text,
        ts=ts, status='delivered', source=source, data=data,
    )


def test_lista_muestra_icono_de_canal_y_css_de_tarjetas():
    _ev('584121111111', name='Carla Test', text='Hola por WhatsApp', canal='whatsapp', hours=3)
    _ev('ig:cidLuna', name='luna.demo', text='¿Sigue disponible?', canal='instagram', hours=1)
    _ev('fb:cidFb', name='fb.demo.perez', text='Consulta por Facebook', canal='facebook', hours=2)
    _ev('email:demo@forrent.test', name='demo@forrent.test', text='Asunto: visita', canal='email', hours=4)
    convos = appmod.panel_conversations({'user': 'jose', 'role': 'admin'})
    por = {c['wa_id']: c for c in convos}
    assert por['ig:cidLuna']['canal'] == 'instagram' and por['ig:cidLuna']['canal_label'] == 'IG'
    assert por['fb:cidFb']['canal'] == 'facebook'
    assert por['email:demo@forrent.test']['canal'] == 'email'
    assert por['584121111111']['canal'] == 'whatsapp'
    assert convos[0]['wa_id'] == 'ig:cidLuna'
    html = cliente('jose').get('/panel').get_data(as_text=True)
    assert 'border-radius:14px' in html
    assert 'overscroll-behavior-y:contain' in html
    assert 'ptr-slot' in html and 'ptr-spinner' in html
    assert 'convo skeleton' in html and '@keyframes shimmer' in html
    assert "passive: true" in html or "{passive: true}" in html
    assert 'PTR_THRESHOLD = 70' in html
    assert 'pullRefresh' in html and '/panel/sincronizar' in html
    assert 'data-canal="instagram"' in html
    assert 'badge-ch instagram' in html


def test_sincronizar_endpoint_refresca_lista(monkeypatch):
    _ev('584121111111', name='Carla Test', text='Antes', canal='whatsapp', hours=2)
    llamado = {'n': 0}

    def fake_sync(*a, **k):
        llamado['n'] += 1
        _ev('ig:cidNuevo', name='nuevo.ig', text='Llegó por GHL', canal='instagram', hours=0)
        return {'ok': True, 'insertadas': 1}

    monkeypatch.setattr(appmod.ghl_sync, 'sincronizar', fake_sync)
    monkeypatch.setattr(appmod.ghl_sync, 'configurado', lambda: True)
    r = cliente('jose').post('/panel/sincronizar', headers={'X-CSRF-Token': csrf('jose')})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True and llamado['n'] == 1
    ids = {c['wa_id'] for c in d['conversations']}
    assert 'ig:cidNuevo' in ids and '584121111111' in ids


def test_webhook_no_duplica_si_ya_vino_por_ghl(monkeypatch):
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    appmod.insert_event(
        kind='message', wamid='ghl:prev', wa_id='584121111111', name='Carla Test',
        direction='inbound', msg_type='text', text='Hola', ts=NOW.isoformat(),
        source='ghl_sync', data={'meta_wamid': 'wamid.DUP', 'channel': 'whatsapp'},
    )
    antes = appmod.q("SELECT COUNT(*) AS n FROM wa_events WHERE kind='message'", fetch=True)[0]['n']
    assert appmod.ghl_sync.existe_meta_ya(appmod.q, 'wamid.DUP', is_pg=False) is True
    body = {'entry': [{'changes': [{'value': {
        'messages': [{'id': 'wamid.DUP', 'from': '584121111111', 'type': 'text',
                      'text': {'body': 'Hola'}, 'timestamp': '1732212000'}],
        'contacts': [{'wa_id': '584121111111', 'profile': {'name': 'Carla Test'}}],
    }}]}]}
    raw = json.dumps(body).encode()
    # sin META_APP_SECRET el webhook acepta
    c = appmod.app.test_client()
    r = c.post('/webhook', data=raw, content_type='application/json')
    assert r.status_code == 200
    despues = appmod.q("SELECT COUNT(*) AS n FROM wa_events WHERE kind='message'", fetch=True)[0]['n']
    assert despues == antes


def test_responder_instagram_usa_tipo_ig(monkeypatch):
    monkeypatch.setenv('GHL_API_TOKEN', 'x')
    monkeypatch.setenv('GHL_LOCATION_ID', 'loc')
    _ev('ig:cidLuna', name='luna.demo', text='hola', canal='instagram', hours=1, mid='ghl:in-luna')
    llamadas = []

    class Fake:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({'messageId': 'ig-out-1'}).encode()
        status = 200

    def fake_call(method, path, *, params=None, body=None, opener=None):
        llamadas.append((method, path, body))
        return 201, {'messageId': 'ig-out-1'}

    monkeypatch.setattr(bandeja, '_ghl_call', fake_call)
    r = cliente('jose').post('/panel/responder',
                             data={'c': 'ig:cidLuna', 'texto': 'Claro, te ayudo', 'csrf': csrf('jose')},
                             headers={'Accept': 'application/json'})
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert llamadas and llamadas[0][2]['type'] == 'IG'
    assert llamadas[0][2]['contactId'] == 'cid-ig:cidLu'
    row = appmod.q("SELECT * FROM wa_events WHERE wamid=%s", ('ghl:ig-out-1',), fetch=True)[0]
    assert row['wa_id'] == 'ig:cidLuna' and row['source'] == 'portal'


def test_estado_ig_sin_ghl_sigue_deshabilitado():
    est = bandeja.estado_respuesta('instagram', NOW.isoformat(), NOW)
    assert est['puede_enviar'] is False
