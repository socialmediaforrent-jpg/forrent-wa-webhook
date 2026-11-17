"""Salientes enviados desde GHL: Meta solo manda estados (sin texto). Caso real Yasenka 17-nov (datos anonimizados)."""
import json, os, sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bandeja import appmod, cliente, entorno, CLI_ANA  # noqa: E402,F401

WA = CLI_ANA
HOY = datetime.now(timezone.utc).strftime('%Y-%m-%d')


def ev(kind, wamid, ts, **kw):
    base = dict(kind=kind, wamid=wamid, wa_id=WA, name='Cliente', direction='inbound' if kind == 'message' else 'outbound',
                msg_type='text', text='', ts=f'{HOY}T{ts}+00:00', status='', source='meta', data={})
    base.update(kw)
    appmod.insert_event(**base)


def caso_yasenka():
    ev('message', 'wamid.in1', '16:01:34', text='¡Hola! Quiero más información')
    for st, t in (('delivered', '16:03:12'), ('sent', '16:03:11'), ('read', '16:51:19')):
        ev('status', 'wamid.out1', t, status=st, msg_type=None)
    ev('message', 'wamid.in2', '16:51:35', text='Hola Mónica')
    ev('message', 'wamid.in3', '16:52:26', text='3 hab. Cannon hasta 750')
    for st, t in (('sent', '16:57:58'), ('delivered', '16:58:07'), ('read', '16:58:20')):
        ev('status', 'wamid.out2', t, status=st, msg_type=None)


def hilo():
    msgs, _, _ = appmod.panel_thread(WA)
    return msgs


def test_salientes_de_ghl_aparecen_como_burbuja_enviada():
    caso_yasenka()
    m = hilo()
    assert [x['direction'] for x in m] == ['in', 'out', 'in', 'in', 'out']
    out = [x for x in m if x['direction'] == 'out']
    assert all(x['text'] == appmod.GHL_PENDIENTE_TXT for x in out)
    assert [x['status'] for x in out] == ['Leído', 'Leído']   # el mejor estado, no el último que llegó
    # hora de Caracas (UTC-4), la misma que usa el resto del portal
    assert m[0]['time'] == '12:01 p. m.' and out[0]['time'] == '12:03 p. m.' and out[1]['time'] == '12:57 p. m.'


def test_sync_con_meta_wamid_reemplaza_el_marcador_sin_duplicar():
    caso_yasenka()
    ev('outbound', 'ghl:abc', '16:03:09.538', source='ghl_import', text='¡Hola, Yasenka! Soy Mónica',
       data={'meta_wamid': 'wamid.out1'})
    m = hilo()
    out = [x for x in m if x['direction'] == 'out']
    assert len(out) == 2
    assert out[0]['text'] == '¡Hola, Yasenka! Soy Mónica' and out[0]['status'] == 'Leído'
    assert out[1]['text'] == appmod.GHL_PENDIENTE_TXT


def test_historico_ghl_sin_wamid_se_empareja_por_hora():
    caso_yasenka()
    ev('outbound', 'ghl:viejo', '16:57:56.918', source='ghl_import', text='Anotado: 3 habitaciones')
    out = [x for x in hilo() if x['direction'] == 'out']
    assert len(out) == 2
    assert out[1]['text'] == 'Anotado: 3 habitaciones' and out[1]['status'] == 'Leído'


def test_envio_del_portal_no_se_duplica():
    ev('message', 'wamid.in1', '16:01:34', text='Hola')
    ev('outbound', 'wamid.portal1', '16:05:00', source='portal', text='Respuesta portal')
    ev('status', 'wamid.portal1', '16:05:01', status='delivered', msg_type=None)
    out = [x for x in hilo() if x['direction'] == 'out']
    assert len(out) == 1 and out[0]['text'] == 'Respuesta portal' and out[0]['status'] == 'Entregado'


def test_lista_muestra_tu_y_no_leidos_cuentan_desde_lo_enviado_en_ghl():
    caso_yasenka()
    ev('message', 'wamid.in4', '16:59:28', text='podemos conversar')
    for st, t in (('sent', '17:12:31'), ('read', '17:13:58')):
        ev('status', 'wamid.out3', t, status=st, msg_type=None)
    convos = appmod.panel_conversations({'user': 'jose', 'role': 'admin'})
    c = next(x for x in convos if x['wa_id'] == WA)
    assert c['preview'] == 'Tú: Mensaje enviado desde GHL'
    assert c['time_full'] == '1:12 p. m.'
    assert c['unread'] == 0
    ev('message', 'wamid.in5', '17:20:29', text='otras opciones')
    convos = appmod.panel_conversations({'user': 'jose', 'role': 'admin'})
    c = next(x for x in convos if x['wa_id'] == WA)
    assert c['unread'] == 1 and c['preview'] == 'otras opciones'


def test_panel_data_json_lleva_burbujas_out():
    caso_yasenka()
    r = cliente('jose').get('/panel/data?c=' + WA)
    assert r.status_code == 200
    d = r.get_json()
    assert [m['direction'] for m in d['messages']].count('out') == 2
