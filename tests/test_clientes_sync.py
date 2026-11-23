"""Sincronización periódica de estatus desde el Sistema de Clientes."""
import json
import clientes_sync


def test_aplicar_respuestas_actualiza_estatus_y_no_toca_al_resto():
    clientes = [
        {'id': 'a1', 'nombre': 'Astrid Cardona', 'estatus': 'Sin estatus', 'estado': 'Sin estatus', 'situacion': '',
         'asignado_at': '2026-11-22T17:07:27'},
        {'id': 'c1', 'nombre': 'Charly Melo', 'estatus': 'Sin estatus', 'estado': 'Sin estatus', 'situacion': '',
         'asignado_at': '2026-11-22T18:23:54'},
        {'id': 'x1', 'nombre': 'Sin cambio', 'estatus': 'Buscando', 'estado': 'En proceso', 'situacion': 'Buscando',
         'asignado_at': '2026-11-10T10:00:00'},
    ]
    resp = {
        'a1': {'estado': 'En proceso', 'situacion': 'Contactado', 'at': '2026-11-22T20:00:00-04:00'},
        'otro': {'estado': 'Perdido', 'situacion': 'Esperando'},
    }
    out, n = clientes_sync.aplicar_respuestas(clientes, resp)
    assert n == 1
    assert out[0]['estatus'] == 'Contactado' and out[0]['estado'] == 'En proceso'
    assert out[1]['nombre'] == 'Charly Melo' and out[1]['estatus'] == 'Sin estatus'
    assert out[2]['estatus'] == 'Buscando'
    data, n2 = clientes_sync.aplicar_a_payload({'clientes': clientes, 'fuente': 'x'}, resp)
    assert n2 == 1 and data['respuestas_en_vivo'] is True
    assert 'actualizado' in data and 'T' in data['actualizado']


def test_pasada_guarda_en_portal_datos(tmp_path, monkeypatch):
    store = {}

    def q(sql, args=(), fetch=False):
        if fetch and 'SELECT datos' in sql:
            d = store.get(args[0]) if args else None
            return [{'datos': d}] if d is not None else []
        if 'INSERT' in sql or 'OR REPLACE' in sql:
            store[args[0]] = args[1]
            return None
        return []

    store['clientes_asignados.json'] = {
        'clientes': [{'id': 'a1', 'nombre': 'Laura Sánchez', 'estatus': 'Sin estatus',
                      'estado': 'Sin estatus', 'situacion': '', 'asignado_at': '2026-11-22T17:38:23'}],
        'actualizado': '2026-11-15T20:52:52-04:00',
    }

    def fake_urlopen(req, timeout=45):
        class R:
            def read(self):
                return json.dumps({'a1': {'estado': 'En proceso', 'situacion': 'Contactado'}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setenv('CLIENTES_ESTATUS_SYNC_URL', 'https://estatus.example.test')
    monkeypatch.setenv('CLIENTES_ESTATUS_SYNC_KEY', 'k')
    res = clientes_sync.pasada(q, is_pg=False, opener=fake_urlopen, also_crm=False)
    assert res['ok'] and res['cambiados'] == 1 and res['n'] == 1
    guardado = store['clientes_asignados.json']
    assert guardado['clientes'][0]['estatus'] == 'Contactado'
    assert guardado['actualizado'] != '2026-11-15T20:52:52-04:00'
