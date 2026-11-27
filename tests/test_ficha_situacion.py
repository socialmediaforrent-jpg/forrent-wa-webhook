"""Tarjeta del cliente asignado, actualización de Situación y recarga de la PWA."""
import json
import os

import estatus_modelo
import clientes_sync
from test_asignaciones import CLIENTES_FIXTURE, _carga, _lista_visible
from test_bandeja import appmod, cliente, csrf

ANA_OK = next(c for c in CLIENTES_FIXTURE if c['id'] == 'b1')  # Busca Ana
LUZ = next(c for c in CLIENTES_FIXTURE if c['id'] == 'n2')


def _limpia_portal():
    appmod.q("DELETE FROM portal_datos WHERE nombre IN (%s, %s)",
             ('clientes_asignados.json', clientes_sync.HIST_NOMBRE))
    appmod._live_cache['t'] = 0.0
    appmod._live_cache['d'] = {}
    clientes_sync.reset_memoria()


def _sembrar(extras=None):
    _limpia_portal()
    data = {'clientes': list(CLIENTES_FIXTURE) + list(extras or []),
            'actualizado': '2026-11-27T12:00:00-04:00'}
    appmod.q('INSERT OR REPLACE INTO portal_datos (nombre, datos, actualizado) VALUES (%s, %s, %s)',
             ('clientes_asignados.json', data, data['actualizado']))
    appmod._live_cache['t'] = 0.0
    return data


def _post(slug, cid, datos, *, ver_como=None, token=None):
    c = cliente(slug)
    if ver_como:
        assert c.post('/ver-como', data={'csrf': csrf(slug), 'asesora': ver_como}).status_code == 302
        token = token or csrf(ver_como)
    else:
        token = token or csrf(slug)
    payload = {'csrf': token, 'volver': '/mis-asignaciones', **datos}
    return c, c.post(f'/asesor/asignados/{cid}/situacion', data=payload, follow_redirects=False)


# --- modelo ---------------------------------------------------------------

def test_validar_igual_que_el_sistema():
    assert estatus_modelo.validar({'estado': 'Nuevo', 'situacion': 'Contactado'}) is None
    assert estatus_modelo.validar({'estado': 'En proceso', 'situacion': 'Buscando'}) is None
    assert estatus_modelo.validar({'estado': 'En proceso', 'situacion': 'Esperando'}) is None
    assert 'estado' in (estatus_modelo.validar({'estado': '', 'situacion': 'Contactado'}) or '').lower()
    assert 'situación' in (estatus_modelo.validar({'estado': 'Nuevo', 'situacion': ''}) or '').lower()
    assert 'fecha' in (estatus_modelo.validar({
        'estado': 'En proceso', 'situacion': 'Visita agendada'}).lower())
    assert estatus_modelo.validar({
        'estado': 'En proceso', 'situacion': 'Visita agendada', 'fecha': '2026-11-27'}) is None
    assert 'detalle' in (estatus_modelo.validar({'estado': 'Perdido'}) or '').lower()
    assert estatus_modelo.validar({'estado': 'Perdido', 'motivo': 'Zona'}) is None
    assert 'Comentario' in (estatus_modelo.validar({'estado': 'Perdido', 'motivo': 'Otro'}) or '')
    rec = estatus_modelo.registro(
        {'estado': 'En proceso', 'situacion': 'Buscando', 'comentario': 'Llamé hoy'},
        asesor='Ana I. Teixeira', at='2026-11-27T20:00:00+00:00')
    assert rec['estado'] == 'En proceso' and rec['situacion'] == 'Buscando'
    assert rec['comentario'] == 'Llamé hoy' and rec['asesor'] == 'Ana I. Teixeira'


# --- tarjeta en la lista --------------------------------------------------

def test_tarjeta_asignado_tiene_campos_e_info(monkeypatch):
    _carga(monkeypatch, extras=[{
        'id': 'info1', 'nombre': 'Carla Demo', 'asesor': 'ana',
        'estado': 'Nuevo', 'situacion': 'Contactado', 'estatus': 'Contactado',
        'asignado_at': '2026-11-20', 'operacion': 'Alquiler', 'tipo_lead': 'Búsqueda',
        'inmueble': 'Búsqueda de ALQUILER. Cliente directo. 3 habitaciones en La Florida, US$750. '
                    'OJO: confirmar horario. WhatsApp del cliente: +58 414-0001111. '
                    'Puede retomar Los Caobos Rex 3927474542 (3 hab, US$800).',
    }])
    html = cliente('ana').get('/mis-asignaciones?e=contactado').get_data(as_text=True)
    assert 'Carla Demo' in html
    assert 'Información del cliente' in html
    assert 'Actualizar situación' in html
    assert 'Copia de prueba: no se envía al Sistema de Clientes.' in html
    vis = _lista_visible(html)
    assert 'WhatsApp' in vis or 'wa.me' in vis
    assert 'Tipo de lead' in vis
    assert 'Inmueble o búsqueda' in vis or 'Inmueble' in vis
    assert 'Fecha de asignación' in vis or 'Asignado' in vis
    assert 'Estatus actual' in vis
    assert 'Historial' in vis
    # Ficha ordenada (no un solo párrafo BUSCA)
    assert 'Qué busca' in html or 'Qué busca' in vis
    assert 'Los Caobos' in html
    assert 'Rex 3927474542' in html
    assert 'Resumen' in html
    # PWA: versión y limpieza de service workers
    assert 'name="app-build"' in html
    assert 'serviceWorker' in html
    assert "cache:'no-store'" in html or 'cache:\'no-store\'' in html


def test_html_no_se_guarda_en_cache_y_app_build_publico():
    r = cliente('ana').get('/mis-asignaciones')
    assert r.status_code == 200
    assert 'no-store' in (r.headers.get('Cache-Control') or '')
    b = appmod.app.test_client().get('/app-build')
    assert b.status_code == 200
    assert 'no-store' in (b.headers.get('Cache-Control') or '')
    body = b.get_json()
    assert body and body.get('build')
    assert body['build'] == appmod.APP_BUILD


# --- escritura ------------------------------------------------------------

def test_prueba_guarda_local_y_no_envia_al_sistema(monkeypatch):
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)
    _sembrar()
    enviados = []
    monkeypatch.setattr(clientes_sync, 'enviar_respuesta', lambda *a, **k: enviados.append(a) or True)
    c, r = _post('ana', 'b1', {
        'estado': 'En proceso', 'situacion': 'Esperando', 'comentario': 'Espera respuesta del captador',
    })
    assert r.status_code == 302, r.get_data(as_text=True)
    loc = r.headers.get('Location') or ''
    assert 'ok=situacion_prueba' in loc
    assert 'abrir=b1' in loc
    assert enviados == []
    hist = clientes_sync.leer_historial(appmod.q)
    assert hist.get('b1') and hist['b1'][-1]['situacion'] == 'Esperando'
    assert hist['b1'][-1]['comentario'] == 'Espera respuesta del captador'
    assert hist['b1'][-1]['asesor'] in ('ana', 'Ana I. Teixeira', 'Ana')
    # La página refleja el cambio y reabre la tarjeta
    page = c.get(loc).get_data(as_text=True)
    assert 'Situación actualizada en la copia de prueba' in page
    assert 'Espera respuesta del captador' in page
    assert 'Esperando' in page
    _limpia_portal()


def test_con_escritura_envia_al_sistema(monkeypatch):
    monkeypatch.setenv('CLIENTES_ESTATUS_WRITE', '1')
    _sembrar()
    enviados = []

    def fake_enviar(q, cid, rec, opener=None):
        enviados.append((cid, rec))
        return True

    monkeypatch.setattr(clientes_sync, 'enviar_respuesta', fake_enviar)
    _, r = _post('ana', 'b1', {'estado': 'Nuevo', 'situacion': 'Contactado', 'comentario': 'Ya hablé'})
    assert r.status_code == 302
    assert 'ok=situacion' in (r.headers.get('Location') or '')
    assert 'situacion_prueba' not in (r.headers.get('Location') or '')
    assert enviados and enviados[0][0] == 'b1'
    assert enviados[0][1]['situacion'] == 'Contactado'
    _limpia_portal()
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)


def test_si_el_sistema_falla_no_aplica_en_silencio(monkeypatch):
    monkeypatch.setenv('CLIENTES_ESTATUS_WRITE', '1')
    _sembrar()

    def boom(*a, **k):
        raise RuntimeError('caído')

    monkeypatch.setattr(clientes_sync, 'enviar_respuesta', boom)
    _, r = _post('ana', 'b1', {'estado': 'Nuevo', 'situacion': 'Contactado'})
    assert r.status_code == 302
    loc = r.headers.get('Location') or ''
    assert 'err=' in loc
    assert 'Sistema de Clientes' in loc or 'guardar' in loc.lower()
    assert not (clientes_sync.leer_historial(appmod.q).get('b1') or [])
    _limpia_portal()
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)


def test_reserva_firmada_se_rechaza():
    _sembrar()
    _, r = _post('ana', 'b1', {
        'estado': 'Negociación', 'situacion': 'Reserva firmada', 'fecha': '2026-11-27',
    })
    assert r.status_code == 302
    loc = r.headers.get('Location') or ''
    from urllib.parse import unquote
    assert 'err=' in loc
    assert 'Reserva firmada' in unquote(loc)
    assert not (clientes_sync.leer_historial(appmod.q).get('b1') or [])
    _limpia_portal()


def test_validacion_fecha_y_permisos(monkeypatch):
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)
    _sembrar()
    _, r = _post('ana', 'b1', {'estado': 'En proceso', 'situacion': 'Visita agendada'})
    assert r.status_code == 302
    assert 'err=' in (r.headers.get('Location') or '')
    # Ana no puede tocar el cliente de Luz
    _, r2 = _post('ana', 'n2', {'estado': 'Nuevo', 'situacion': 'Contactado'})
    assert r2.status_code == 403
    # Sin CSRF
    c = cliente('ana')
    r3 = c.post('/asesor/asignados/b1/situacion', data={
        'estado': 'Nuevo', 'situacion': 'Contactado', 'volver': '/mis-asignaciones',
    }, follow_redirects=False)
    assert r3.status_code == 302
    assert 'sesi' in (r3.headers.get('Location') or '').lower() or 'err=' in (r3.headers.get('Location') or '')
    _limpia_portal()


def test_ver_como_queda_a_nombre_del_admin(monkeypatch):
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)
    _sembrar()
    c, r = _post('jose', 'b1', {
        'estado': 'Nuevo', 'situacion': 'Contactado', 'comentario': 'Visto por presidencia',
    }, ver_como='ana')
    assert r.status_code == 302, r.get_data(as_text=True)
    assert 'ok=situacion_prueba' in (r.headers.get('Location') or '')
    rec = clientes_sync.leer_historial(appmod.q)['b1'][-1]
    # El autor es el admin real, no Ana.
    assert rec.get('por') == 'jose'
    assert rec['asesor'] in ('jose', 'José Daniel Pinto Chacón', 'José')
    assert rec['asesor'] not in ('ana', 'Ana I. Teixeira')
    assert 'viendo como' in (rec.get('via') or '').lower()
    audit = appmod.q(
        'SELECT admin_slug, view_as, action, path FROM ver_como_audit WHERE path LIKE %s',
        ('/asesor/asignados/%',), fetch=True) or []
    escrituras = [a for a in audit if a['action'] == 'escribir']
    assert escrituras, audit
    assert all(a['admin_slug'] == 'jose' and a['view_as'] == 'ana' for a in escrituras)
    _limpia_portal()


def test_escritura_habilitada_solo_con_flag(monkeypatch):
    monkeypatch.delenv('CLIENTES_ESTATUS_WRITE', raising=False)
    assert clientes_sync.escritura_habilitada() is False
    monkeypatch.setenv('CLIENTES_ESTATUS_WRITE', '1')
    assert clientes_sync.escritura_habilitada() is True
    monkeypatch.setenv('CLIENTES_ESTATUS_WRITE', '0')
    assert clientes_sync.escritura_habilitada() is False
