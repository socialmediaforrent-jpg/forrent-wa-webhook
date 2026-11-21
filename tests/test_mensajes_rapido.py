"""Rendimiento de la lista de Mensajes: menos consultas, mismos datos y permisos."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from test_bandeja import CLI_ANA, CLI_OTRO, appmod, cliente, entorno, entrante  # noqa: F401

ADMIN = {'user': 'jose', 'role': 'admin'}
ANA = {'user': 'ana', 'role': 'asesor'}
HOY = datetime.now(timezone.utc)


def _ev(wa, i, *, direction='inbound', kind='message', text='Hola', hours=1, status='', source='meta', data=None, name='Cliente'):
    ts = (HOY - timedelta(hours=hours, seconds=i)).isoformat()
    appmod.insert_event(
        kind=kind, wamid=f'{kind}-{wa}-{i}', wa_id=wa, name=name if direction == 'inbound' else '',
        direction=direction, msg_type='text', text=text, ts=ts, status=status,
        source=source, data=data or {},
    )


def _poblar(n_conv=120, n_msg=12):
    """Muchas conversaciones con historial, más los dos clientes del fixture."""
    for k in range(n_conv):
        wa = f'58412000{k:04d}'
        for i in range(n_msg):
            _ev(wa, i, hours=n_msg - i, text=f'msg {i}')
        _ev(wa, 900, direction='outbound', kind='outbound', text='respuesta', hours=0, name='')
        _ev(wa, 901, kind='status', direction='outbound', status='delivered', hours=0, name='', text='')
    entrante(CLI_ANA, horas_atras=0.2, texto='de ana')
    entrante(CLI_OTRO, horas_atras=0.1, texto='de otro')


def test_lista_acotada_en_consultas_con_mucho_historial():
    _poblar(80, 15)
    appmod._query_count = 0
    t0 = time.perf_counter()
    convos = appmod.panel_conversations(ADMIN)
    ms = (time.perf_counter() - t0) * 1000
    nq = appmod._query_count
    # 80 sintéticas + 2 del fixture
    assert len(convos) == 82
    # No recorre la tabla una vez por conversación: un puñado de consultas agregadas.
    assert nq <= 8, nq
    assert ms < 2000, ms
    ids = {c['wa_id'] for c in convos}
    assert CLI_ANA in ids and CLI_OTRO in ids and '584120000000' in ids


def test_html_primera_pagina_y_boot_con_todas():
    appmod.LISTA_HTML_MAX = 8
    try:
        _poblar(20, 3)
        r = cliente('jose').get('/panel')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'id="inbox-boot"' in html
        data = r.context.get('inbox_boot') if hasattr(r, 'context') else None
        # Flask test client no siempre expone context; leemos /panel/data
        d = cliente('jose').get('/panel/data').get_json()
        assert len(d['conversations']) == 22
        # El HTML no repite las 22 filas (solo las primeras N + posible actual)
        assert html.count('class="convo') <= 10
        assert 'rel="prefetch"' not in html  # ya estamos en Mensajes
    finally:
        appmod.LISTA_HTML_MAX = 80


def test_permisos_iguales_admin_ve_sin_asignar_asesora_no():
    entrante(CLI_ANA, horas_atras=1, texto='ana')
    entrante(CLI_OTRO, horas_atras=1, texto='otro')
    entrante('584129999999', horas_atras=1, texto='sin asignar')
    admin = {c['wa_id'] for c in appmod.panel_conversations(ADMIN)}
    ana = {c['wa_id'] for c in appmod.panel_conversations(ANA)}
    assert admin == {CLI_ANA, CLI_OTRO, '584129999999'}
    assert ana == {CLI_ANA}
    r_ana = cliente('ana').get('/panel/data')
    assert r_ana.status_code == 200
    assert {c['wa_id'] for c in r_ana.get_json()['conversations']} == {CLI_ANA}


def test_corte_sigue_ocultando():
    def por_tel():
        return {
            CLI_ANA: [{'id': '1', 'nombre': 'Cliente Ana', 'asesor': 'ana', 'asignado_at': '2026-11-10', 'en_ejercicio': True}],
            '584128888888': [{'id': 'viejo', 'nombre': 'Viejo', 'asesor': 'ana', 'asignado_at': '2026-10-01', 'en_ejercicio': False}],
        }
    import mensajes
    mensajes.clientes_por_telefono = por_tel
    entrante(CLI_ANA, horas_atras=1, texto='ana')
    entrante('584128888888', horas_atras=1, texto='viejo')
    ids = {c['wa_id'] for c in appmod.panel_conversations(ADMIN)}
    assert CLI_ANA in ids and '584128888888' not in ids
    assert '584128888888' not in {c['wa_id'] for c in appmod.panel_conversations(ANA)}


def test_prefetch_en_inicio_no_en_mensajes():
    r = cliente('jose').get('/portal')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'rel="prefetch"' in html and 'href="/panel"' in html
    r2 = cliente('jose').get('/panel')
    assert 'rel="prefetch"' not in r2.get_data(as_text=True)


def test_social_solo_cache_no_llama_a_meta(monkeypatch):
    import bandeja
    llamado = {'n': 0}

    def boom(*a, **k):
        llamado['n'] += 1
        raise AssertionError('no debe llamar a Graph')

    monkeypatch.setenv('META_SOCIAL_INBOX', '1')
    monkeypatch.setenv('META_PAGE_TOKEN', 'tok')
    monkeypatch.setattr(bandeja, '_graph_get', boom)
    bandeja._cache.clear()
    assert bandeja.social_conversaciones(solo_cache=True) == []
    assert llamado['n'] == 0
    # Sin solo_cache sí intentaría Graph (y fallaría); lo dejamos cubierto por test_bandeja.


def test_panel_data_incluye_todas_y_es_rapido():
    _poblar(40, 8)
    t0 = time.perf_counter()
    r = cliente('jose').get('/panel/data')
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    d = r.get_json()
    assert len(d['conversations']) == 42
    assert ms < 2000, ms
