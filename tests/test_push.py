"""Avisos push al asignar un cliente: suscripción, detección de asignaciones nuevas, limpieza y permisos."""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_bandeja import cliente, csrf, appmod  # noqa: E402
import push  # noqa: E402

SUB = {'endpoint': 'https://fcm.googleapis.com/fcm/send/abc123', 'keys': {'p256dh': 'BPk' + 'x' * 80, 'auth': 'authkey123456'}}


@pytest.fixture(autouse=True)
def vapid(monkeypatch):
    monkeypatch.setenv('VAPID_PUBLIC_KEY', 'BPublicKeyDePrueba')
    monkeypatch.setenv('VAPID_PRIVATE_KEY', 'privada-de-prueba')
    for t in ('push_subs', 'push_asig_vistas'):
        appmod.q(f'DELETE FROM {t}')
    yield
    for t in ('push_subs', 'push_asig_vistas'):
        appmod.q(f'DELETE FROM {t}')


def _suscribir(slug='ana', sub=SUB):
    return cliente(slug).post('/push/suscribir', data={'csrf': csrf(slug), 'sub': json.dumps(sub)})


def test_sw_js_solo_push_sin_cache_ni_fetch():
    r = appmod.app.test_client().get('/sw.js')
    assert r.status_code == 200
    assert 'javascript' in r.headers['Content-Type']
    assert 'no-cache' in r.headers['Cache-Control']
    js = r.get_data(as_text=True)
    assert "addEventListener('push'" in js and "addEventListener('notificationclick'" in js
    assert "addEventListener('fetch'" not in js and 'caches.' not in js


def test_boton_visible_solo_con_claves_y_para_asesor():
    html = cliente('ana').get('/portal').get_data(as_text=True)
    assert 'id="push-aviso"' in html and 'Activar notificaciones' in html
    # El admin real no lo ve (no tiene clientes asignados).
    assert 'id="push-aviso"' not in cliente('admin').get('/portal').get_data(as_text=True)


def test_sin_claves_vapid_no_hay_boton_ni_suscripcion(monkeypatch):
    monkeypatch.delenv('VAPID_PRIVATE_KEY')
    assert 'id="push-aviso"' not in cliente('ana').get('/portal').get_data(as_text=True)
    assert _suscribir().status_code == 403


def test_suscribir_requiere_csrf_y_datos_validos():
    assert cliente('ana').post('/push/suscribir', data={'csrf': 'mal', 'sub': json.dumps(SUB)}).status_code == 400
    assert _suscribir(sub={'endpoint': 'http://inseguro', 'keys': {}}).status_code == 400
    r = _suscribir()
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'dispositivos': 1}
    # El mismo dispositivo no se duplica.
    assert _suscribir().get_json()['dispositivos'] == 1
    assert _suscribir(sub={**SUB, 'endpoint': SUB['endpoint'] + 'otro'}).get_json()['dispositivos'] == 2


def test_baja_borra_solo_lo_propio():
    _suscribir()
    cliente('luz').post('/push/baja', data={'csrf': csrf('luz'), 'endpoint': SUB['endpoint']})
    assert push.n_dispositivos(appmod.q, 'ana') == 1
    cliente('ana').post('/push/baja', data={'csrf': csrf('ana'), 'endpoint': SUB['endpoint']})
    assert push.n_dispositivos(appmod.q, 'ana') == 0


def test_primera_pasada_solo_marca_base_y_luego_avisa_lo_nuevo(monkeypatch):
    enviados = []
    monkeypatch.setattr(push, '_enviar_uno', lambda info, payload: enviados.append(json.loads(payload)) or 201)
    appmod.q("INSERT INTO push_subs (asesor_slug, endpoint, p256dh, auth, created_at) VALUES ('ana', %s, 'p', 'a', 'x')", (SUB['endpoint'],))
    lista = [{'id': 'c1', 'asesor': 'ana', 'nombre': 'Uno'}, {'id': 'c2', 'asesor': 'ana', 'nombre': 'Dos'}]
    r = push.revisar_asignaciones(appmod.q, lista)
    assert r['base'] and r['nuevos'] == 0 and enviados == []
    lista.append({'id': 'c3', 'asesor': 'ana', 'nombre': 'María Pérez'})
    r = push.revisar_asignaciones(appmod.q, lista)
    assert (r['base'], r['nuevos'], r['enviados'], r['codigos']) == (False, 1, 1, [201])
    assert enviados[0]['body'] == 'Nuevo cliente asignado: María Pérez'
    assert enviados[0]['url'] == '/mis-clientes?abrir=c3'
    # No se repite.
    assert push.revisar_asignaciones(appmod.q, lista)['nuevos'] == 0 and len(enviados) == 1


def test_varios_dispositivos_y_suscripciones_vencidas_se_limpian(monkeypatch):
    codigos = {'https://a.example/1': 201, 'https://b.example/2': 410, 'https://c.example/3': 404}
    monkeypatch.setattr(push, '_enviar_uno', lambda info, payload: codigos[info['endpoint']])
    for ep in codigos:
        appmod.q("INSERT INTO push_subs (asesor_slug, endpoint, p256dh, auth, created_at) VALUES ('ana', %s, 'p', 'a', 'x')", (ep,))
    push.revisar_asignaciones(appmod.q, [{'id': 'c1', 'asesor': 'ana', 'nombre': 'Uno'}])
    r = push.revisar_asignaciones(appmod.q, [{'id': 'c1', 'asesor': 'ana', 'nombre': 'Uno'}, {'id': 'c2', 'asesor': 'ana', 'nombre': 'Dos'}])
    assert sorted(r['codigos']) == [201, 404, 410]
    quedan = [x['endpoint'] for x in appmod.q('SELECT endpoint FROM push_subs', fetch=True)]
    assert quedan == ['https://a.example/1']


def test_asignacion_de_otro_asesor_no_avisa_a_ana(monkeypatch):
    llamadas = []
    monkeypatch.setattr(push, '_enviar_uno', lambda i, p: llamadas.append(1) or 201)
    appmod.q("INSERT INTO push_subs (asesor_slug, endpoint, p256dh, auth, created_at) VALUES ('ana', %s, 'p', 'a', 'x')", (SUB['endpoint'],))
    push.revisar_asignaciones(appmod.q, [{'id': 'c1', 'asesor': 'luz', 'nombre': 'Uno'}])
    push.revisar_asignaciones(appmod.q, [{'id': 'c1', 'asesor': 'luz', 'nombre': 'Uno'}, {'id': 'c2', 'asesor': 'luz', 'nombre': 'Dos'}])
    assert llamadas == []


def test_portal_sync_dispara_el_aviso(monkeypatch):
    monkeypatch.setenv('PORTAL_SYNC_KEY', 'llave-test')
    monkeypatch.setattr(push, '_enviar_uno', lambda i, p: 201)
    appmod.q("INSERT INTO push_subs (asesor_slug, endpoint, p256dh, auth, created_at) VALUES ('ana', %s, 'p', 'a', 'x')", (SUB['endpoint'],))
    hoy = '2026-11-27T12:00:00-04:00'
    appmod.q('DELETE FROM portal_datos WHERE nombre=%s', ('clientes_asignados.json',))
    appmod._live_cache['t'] = 0.0
    import portal_lib
    n_prev = len((portal_lib._load('clientes_asignados.json', {}) or {}).get('clientes') or [])
    base = [{'id': f'k{i}', 'asesor': 'ana', 'nombre': f'N{i}', 'asignado_at': hoy} for i in range(max(3, n_prev))]
    c = appmod.app.test_client()
    h = {'X-Portal-Sync-Key': 'llave-test'}
    r = c.post('/admin/portal-sync', json={'clientes_asignados.json': {'clientes': base, 'actualizado': hoy}}, headers=h)
    assert r.status_code == 200 and r.get_json()['push']['base'] is True
    r = c.post('/admin/portal-sync', json={'clientes_asignados.json': {'clientes': base + [{'id': 'zz-nuevo', 'asesor': 'ana', 'nombre': 'Nuevo', 'asignado_at': hoy}], 'actualizado': hoy}}, headers=h)
    p = r.get_json()['push']
    assert p['nuevos'] == 1 and p['codigos'] == [201]
    appmod.q('DELETE FROM portal_datos WHERE nombre=%s', ('clientes_asignados.json',))
    appmod._live_cache['t'] = 0.0


def test_scripts_de_la_pagina_son_javascript_valido(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('sin node')
    html = cliente('ana').get('/portal').get_data(as_text=True)
    scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
    assert any('push-aviso' in s for s in scripts) and any('getRegistrations' in s for s in scripts)
    for i, js in enumerate(scripts):
        f = tmp_path / f's{i}.js'
        f.write_text(js)
        r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
