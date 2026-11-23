"""Pruebas del embudo Mis Asignaciones: mapeo, permisos, corte 31-oct y botón de Inicio."""
import portal_lib
from test_bandeja import cliente, appmod, portal_lib as pl

# test_bandeja ya configuró el entorno y cargó app; portal_lib es el mismo módulo.
assert portal_lib is pl


CLIENTES_FIXTURE = [
    {'id': 'n1', 'nombre': 'Nueva Ana', 'asesor': 'ana', 'estado': 'Nuevo', 'situacion': '',
     'estatus': 'Nuevo', 'asignado_at': '2026-11-05', 'operacion': 'Alquiler'},
    {'id': 'n2', 'nombre': 'Sin estatus Luz', 'asesor': 'luz', 'estado': 'Sin estatus', 'situacion': '',
     'estatus': 'Sin estatus', 'asignado_at': '2026-11-02', 'operacion': 'Alquiler'},
    {'id': 'c1', 'nombre': 'Contactada Miriam', 'asesor': 'miriam', 'estado': 'Nuevo', 'situacion': 'Contactado',
     'estatus': 'Contactado', 'asignado_at': '2026-11-08', 'operacion': 'Venta'},
    {'id': 'b1', 'nombre': 'Busca Ana', 'asesor': 'ana', 'estado': 'En proceso', 'situacion': 'Buscando',
     'estatus': 'Buscando', 'asignado_at': '2026-11-10', 'operacion': 'Alquiler'},
    {'id': 'e1', 'nombre': 'Espera Ana', 'asesor': 'ana', 'estado': 'En proceso', 'situacion': 'Esperando',
     'estatus': 'Esperando', 'asignado_at': '2026-11-10', 'operacion': 'Alquiler'},
    {'id': 'v1', 'nombre': 'Agendada Ana', 'asesor': 'ana', 'estado': 'En proceso', 'situacion': 'Visita agendada',
     'estatus': 'Visita agendada', 'asignado_at': '2026-11-11', 'operacion': 'Alquiler'},
    {'id': 'o1', 'nombre': 'Oferta Ana', 'asesor': 'ana', 'estado': 'Negociación', 'situacion': 'Oferta realizada',
     'estatus': 'Oferta realizada', 'asignado_at': '2026-11-12', 'operacion': 'Venta'},
    {'id': 'p1', 'nombre': 'Perdida Ana', 'asesor': 'ana', 'estado': 'Perdido', 'situacion': 'Buscando',
     'estatus': 'Buscando', 'asignado_at': '2026-11-09', 'operacion': 'Alquiler'},
    {'id': 'old', 'nombre': 'Anterior Ana', 'asesor': 'ana', 'estado': 'Nuevo', 'situacion': '',
     'estatus': 'Nuevo', 'asignado_at': '2026-10-30', 'operacion': 'Alquiler'},
]


def _carga(monkeypatch, extras=None):
    data = {'clientes': list(CLIENTES_FIXTURE) + list(extras or []), 'actualizado': '2026-11-16T12:00:00-04:00'}

    def loader(name):
        if name == 'clientes_asignados.json':
            return data
        return None

    monkeypatch.setattr(portal_lib, '_LIVE_LOADER', loader)
    return data


def test_mapeo_estatus():
    m = portal_lib.tile_de_asignacion
    assert m('Nuevo', '', 'Nuevo') == 'nuevas'
    assert m('Sin estatus', '', 'Sin estatus') == 'sin_estatus'
    assert m('', '', '') == 'sin_estatus'
    assert m('Nuevo', 'No contactado', 'No contactado') == 'nuevas'
    assert m('Nuevo', 'Contactado', 'Contactado') == 'contactado'
    assert m('En proceso', 'Buscando', 'Buscando') == 'busqueda'
    assert m('En proceso', 'Visita agendada', 'Visita agendada') == 'agendado'
    assert m('Negociación', 'Oferta realizada', 'Oferta realizada') == 'oferta'
    assert m('Negociación', 'Oferta aceptada', 'Oferta aceptada') == 'oferta_aceptada'
    assert m('', 'Reserva agendada', 'Reserva agendada') == 'reserva_agendada'
    assert m('', 'Reserva firmada', 'Reserva firmada') == 'reserva_firmada'
    assert m('', 'Documento notariado', 'Documento notariado') == 'registrado'
    assert m('', 'Documento registrado', 'Documento registrado') == 'registrado'
    assert m('', 'Inmueble entregado', 'Inmueble entregado') == 'entregado'
    # Reconocidos sin baldosa: no se inventa un casillero ni se mezclan.
    assert m('En proceso', 'Esperando', 'Esperando') is None
    assert m('', 'En espera', 'En espera') is None
    assert m('En proceso', '', 'En proceso') is None
    assert m('En proceso', 'Visita realizada', 'Visita realizada') is None
    assert m('', 'Documento privado', 'Documento privado') is None
    assert m('Negociación', '', 'Negociación') is None
    # Perdido tiene su propia fila, aunque la situación sea un estatus mapeado.
    assert m('Perdido', 'Buscando', 'Buscando') == 'perdidos'
    assert m('Perdido', '', 'Perdido') == 'perdidos'
    assert m('', 'descartado', 'descartado') == 'perdidos'


def test_resumen_no_inventa_ceros():
    r = portal_lib.resumen_asignaciones([
        {'estado': 'Nuevo', 'situacion': '', 'estatus': 'Nuevo', 'nombre': 'A'},
    ])
    tiles = r['tiles']
    assert tiles['nuevas']['n'] == 1
    assert tiles['sin_estatus']['n'] == 0
    assert tiles['contactado']['n'] == 0
    assert tiles['busqueda']['n'] == 0
    assert tiles['agendado']['n'] == 0
    assert tiles['oferta']['n'] == 0
    assert tiles['oferta_aceptada']['n'] == 0
    assert tiles['reserva_agendada']['n'] == 0
    assert tiles['reserva_firmada']['n'] == 0
    assert tiles['registrado']['n'] == 0
    assert tiles['entregado']['n'] == 0
    assert tiles['perdidos']['n'] == 0
    assert r['en_embudo'] == 1
    assert r['total'] == 1
    assert tiles['oferta']['clientes'] == []


def _primera_tarjeta(html):
    """Primera tarjeta de acceso en el contenido de Inicio (no el menú)."""
    import re
    main = html.split('<main', 1)[-1]
    m = re.search(r'<div class="cards[^"]*">\s*<a class="card" href="([^"]+)"', main)
    assert m, 'no hay tarjeta de acceso en Inicio'
    return m.group(1), main


def test_inicio_primer_boton_asesora():
    html = cliente('ana').get('/').get_data(as_text=True)
    href, main = _primera_tarjeta(html)
    assert href == '/mis-asignaciones'
    assert 'Mis Asignaciones' in main
    # En el contenido, Mis Asignaciones va antes de Mi Perfil.
    assert main.find('Mis Asignaciones') < main.find('Mi Perfil')


def test_inicio_primer_boton_admin():
    """El inicio de Presidencia ya no lleva tarjetas; el embudo sigue en /mis-asignaciones."""
    html = cliente('jose').get('/').get_data(as_text=True)
    main = html.split('<main', 1)[-1]
    assert '<em>José</em>, bienvenido a For Rent Online.' in main
    assert 'de For Rent Grupo Inmobiliario' not in main
    assert 'RIF J-50475755-1' not in main
    assert 'inicio-equipo.webp' in main
    assert 'Mis Asignaciones' not in main
    assert 'Resumen de operación' not in main
    assert 'Clientes por fase' not in main
    asig = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    assert 'Mis Asignaciones' in asig


def test_luz_es_admin_y_ve_todas(monkeypatch):
    assert portal_lib.user_by_username('luz')['role'] == 'admin'
    _carga(monkeypatch)
    html = cliente('luz').get('/mis-asignaciones').get_data(as_text=True)
    assert 'Nueva Ana' not in html  # la lista aparece al hacer clic
    # Conteos: nuevas = solo Ana (Nuevo). Luz va a Clientes sin estatus.
    # Ana también tiene buscando, agendada, oferta; perdida y anterior no cuentan en esas baldosas.
    lista = cliente('luz').get('/mis-asignaciones?e=nuevas').get_data(as_text=True)
    assert 'Nueva Ana' in lista
    assert 'Sin estatus Luz' not in lista
    assert 'Contactada Miriam' not in lista
    sin_e = cliente('luz').get('/mis-asignaciones?e=sin_estatus').get_data(as_text=True)
    assert 'Sin estatus Luz' in sin_e
    assert 'Nueva Ana' not in sin_e
    # Luz ve asignados de otras asesoras en el listado (admin).
    todos = cliente('jose').get('/mis-asignaciones?e=contactado').get_data(as_text=True)
    assert 'Contactada Miriam' in todos


def test_asesora_solo_las_suyas(monkeypatch):
    _carga(monkeypatch)
    html = cliente('ana').get('/mis-asignaciones?e=nuevas').get_data(as_text=True)
    assert 'Nueva Ana' in html
    assert 'Sin estatus Luz' not in html
    assert 'Contactada Miriam' not in html
    # Corte: la asignación del 30 oct no aparece.
    assert 'Anterior Ana' not in html
    # Perdida tampoco.
    perd = cliente('ana').get('/mis-asignaciones?e=busqueda').get_data(as_text=True)
    assert 'Busca Ana' in perd
    assert 'Perdida Ana' not in perd
    assert 'Espera Ana' not in perd


def test_conteos_por_rol(monkeypatch):
    _carga(monkeypatch)
    def nums(slug):
        r = cliente(slug).get('/mis-asignaciones')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        # Extrae los 10 números en orden de baldosas del embudo.
        import re
        # las baldosas y la simple tienen <span class="embudo-n">N</span>
        return [int(x) for x in re.findall(r'class="embudo-n">(\d+)</span>', html)]

    # Orden: nuevas, sin_estatus, contactado, busqueda, agendado, oferta,
    # oferta_aceptada, reserva_agendada, reserva_firmada, registrado, entregado, perdidos
    # Ana vigentes: Nueva, Busca, Agendada, Oferta, Perdida. Espera/Anterior no.
    assert nums('ana') == [1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1]
    # José y Luz ven todas las vigentes del fixture (sin anterior ni espera):
    # nuevas 1 (Ana), sin_estatus 1 (Luz), contactado 1 (Miriam), busqueda 1, agendado 1, oferta 1, perdidos 1 (Ana)
    assert nums('jose') == [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1]
    assert nums('luz') == [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1]


def test_mercadeo_no_entra():
    assert cliente('mercadeo').get('/mis-asignaciones').status_code == 403
    assert cliente('mercadeo').get('/mis-asignaciones?e=nuevas').status_code == 403


def test_vista_tiene_fases_del_mockup(monkeypatch):
    _carga(monkeypatch)
    html = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    plano = html.replace('\u200b', '')
    for texto in ('1. Nuevas Asignaciones', 'Clientes sin estatus', '2. En Proceso', '3. En Negociación',
                  '4. Entrega de Inmueble', 'Perdidos', 'Contactado', 'En Búsqueda', 'Agendado',
                  'Oferta enviada/recibida', 'Oferta aceptada', 'Reserva agendada',
                  'Reserva Firmada', 'Inmueble Registrado/Notariado', 'Inmueble Entregado'):
        assert texto in plano, texto
    # Baldosa vacía sigue visible con 0 y es clicable.
    assert 'href="/mis-asignaciones?e=entregado"' in html
    assert 'href="/mis-asignaciones?e=sin_estatus"' in html
    assert 'href="/mis-asignaciones?e=perdidos"' in html
    vacia = cliente('jose').get('/mis-asignaciones?e=entregado').get_data(as_text=True)
    assert 'No hay clientes en este estatus.' in vacia
    assert cliente('jose').get('/mis-asignaciones?e=no-existe').status_code == 200


def test_no_rompe_mis_clientes(monkeypatch):
    _carga(monkeypatch)
    r = cliente('ana').get('/mis-clientes')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'Mis Clientes' in html

def test_texto_corte_vive_en_info(monkeypatch):
    """El párrafo del corte 31-oct no va suelto bajo el título: queda en el popover de info."""
    _carga(monkeypatch)
    texto = 'Embudo comercial de los clientes asignados desde el 31 oct 2026 (reel 19 alquileres). Los anteriores se conservan en el registro, pero no aparecen aquí.'
    for slug in ('jose', 'ana', 'luz'):
        html = cliente(slug).get('/mis-asignaciones').get_data(as_text=True)
        assert texto in html
        assert '<p class="lead">Embudo comercial' not in html
        assert 'class="asig-info-btn"' in html
        assert 'popovertarget="asig-info"' in html
        assert 'id="asig-info"' in html
        assert 'Información de Mis Asignaciones' in html
        # El ícono va junto al título, no suelto en otra sección.
        tit = html.find('class="page-row asig-titulo"')
        info = html.find('class="asig-info-btn"')
        h1 = html.find('>Mis Asignaciones</h1>')
        assert tit != -1 and info != -1 and h1 != -1
        assert tit < h1 < info


def test_embudo_sin_flechas_y_tarjetas_compactas(monkeypatch):
    _carga(monkeypatch)
    html = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    assert 'embudo-flecha' not in html
    # Las 10 baldosas siguen siendo enlaces tocables.
    for tid in ('nuevas', 'sin_estatus', 'contactado', 'busqueda', 'agendado', 'oferta',
                'oferta_aceptada', 'reserva_agendada', 'reserva_firmada',
                'registrado', 'entregado', 'perdidos'):
        assert f'href="/mis-asignaciones?e={tid}"' in html


def test_sin_estatus_y_perdidos_segun_datos(monkeypatch):
    """Las filas nuevas usan nociones ya existentes y no alteran las otras baldosas."""
    _carga(monkeypatch)
    # Sin estatus (ya definido por tiene_estatus / valor «Sin estatus») no se mezcla con Nuevo.
    assert portal_lib.tile_de_asignacion('Sin estatus', '', 'Sin estatus') == 'sin_estatus'
    assert portal_lib.tile_de_asignacion('Nuevo', '', 'Nuevo') == 'nuevas'
    # Perdido (es_perdido / estado=Perdido) no se cuela en En Búsqueda.
    assert portal_lib.tile_de_asignacion('Perdido', 'Buscando', 'Buscando') == 'perdidos'
    assert portal_lib.tile_de_asignacion('En proceso', 'Buscando', 'Buscando') == 'busqueda'
    html = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    assert html.find('1. Nuevas Asignaciones') < html.find('Clientes sin estatus') < html.find('2. En Proceso')
    assert html.find('4. Entrega de Inmueble') < html.find('Perdidos')
    perd = cliente('jose').get('/mis-asignaciones?e=perdidos').get_data(as_text=True)
    assert 'Perdida Ana' in perd
    assert 'Busca Ana' not in perd
    assert 'Anterior Ana' not in perd
