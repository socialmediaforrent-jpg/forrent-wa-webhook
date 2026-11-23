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
    import clientes_sync
    clientes_sync.reset_memoria()
    data = {'clientes': list(CLIENTES_FIXTURE) + list(extras or []), 'actualizado': '2026-11-16T12:00:00-04:00'}

    def loader(name):
        if name == 'clientes_asignados.json':
            return data
        return None

    monkeypatch.setattr(portal_lib, '_LIVE_LOADER', loader)
    return data


def test_mapeo_estatus():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    m = portal_lib.tile_de_asignacion
    ahora = datetime(2026, 11, 22, 21, 0, tzinfo=ZoneInfo('America/Caracas'))
    reciente = '2026-11-22T20:00:00'
    vieja = '2026-11-20T20:00:00'
    # Sin fecha de asignación, o con 24 h o más: Sin estatus (si no hay avance).
    assert m('Nuevo', '', 'Nuevo') == 'sin_estatus'
    assert m('Sin estatus', '', 'Sin estatus') == 'sin_estatus'
    assert m('', '', '') == 'sin_estatus'
    assert m('Nuevo', '', 'Nuevo', '', vieja, now=ahora) == 'sin_estatus'
    assert m('', '', 'ASIGNADO', '', vieja, now=ahora) == 'sin_estatus'
    # Menos de 24 h y sin avance: Nuevas Asignaciones.
    assert m('Nuevo', '', 'Nuevo', '', reciente, now=ahora) == 'nuevas'
    assert m('Nuevo', 'No contactado', 'No contactado', '', reciente, now=ahora) == 'nuevas'
    assert m('', '', 'ASIGNADO', '', reciente, now=ahora) == 'nuevas'
    assert m('', '', 'asignado', '', reciente, now=ahora) == 'nuevas'
    assert m('ASIGNADO', '', 'ASIGNADO', '', reciente, now=ahora) == 'nuevas'
    # Un avance (Contactado, etc.) no entra en Nuevas, aunque sea reciente.
    assert m('Nuevo', 'Contactado', 'Contactado') == 'contactado'
    assert m('Nuevo', 'Contactado', 'Contactado', '', reciente, now=ahora) == 'contactado'
    assert m('En proceso', 'Buscando', 'Buscando') == 'busqueda'
    assert m('En proceso', 'Visita agendada', 'Visita agendada') == 'agendado'
    assert m('Negociación', 'Oferta realizada', 'Oferta realizada') == 'oferta'
    assert m('Negociación', 'Oferta aceptada', 'Oferta aceptada') == 'oferta_aceptada'
    assert m('', 'Reserva agendada', 'Reserva agendada') == 'reserva_agendada'
    assert m('', 'Reserva firmada', 'Reserva firmada') == 'reserva_firmada'
    assert m('', 'Documento notariado', 'Documento notariado') == 'registrado'
    assert m('', 'Documento registrado', 'Documento registrado') == 'registrado'
    assert m('', 'Inmueble entregado', 'Inmueble entregado') == 'entregado'
    assert m('En proceso', 'Esperando', 'Esperando') == 'busqueda'
    assert m('', 'En espera', 'En espera') == 'busqueda'
    assert m('En proceso', 'Visita realizada', 'Visita realizada') == 'agendado'
    # Lo que no tiene baldosa propia cae en Otros, nunca se pierde.
    assert m('En proceso', '', 'En proceso') == 'otros'
    assert m('', 'Documento privado', 'Documento privado') == 'otros'
    assert m('Negociación', '', 'Negociación') == 'otros'
    assert m('', '', 'Estatus inventado XYZ') == 'otros'
    # Perdido tiene su propia fila y no se duplica en Búsqueda/Esperando.
    assert m('Perdido', 'Buscando', 'Buscando') == 'perdidos'
    assert m('Perdido', 'Esperando', 'Esperando') == 'perdidos'
    assert m('Perdido', '', 'Perdido') == 'perdidos'
    assert m('', 'descartado', 'descartado') == 'perdidos'


def test_resumen_no_inventa_ceros():
    r = portal_lib.resumen_asignaciones([
        {'estado': 'Nuevo', 'situacion': '', 'estatus': 'Nuevo', 'nombre': 'A'},
    ])
    tiles = r['tiles']
    # Sin asignado_at cae en Sin estatus, no en Nuevas.
    assert tiles['nuevas']['n'] == 0
    assert tiles['sin_estatus']['n'] == 1
    assert tiles['contactado']['n'] == 0
    assert tiles['busqueda']['n'] == 0
    assert tiles['agendado']['n'] == 0
    assert tiles['oferta']['n'] == 0
    assert tiles['oferta_aceptada']['n'] == 0
    assert tiles['reserva_agendada']['n'] == 0
    assert tiles['reserva_firmada']['n'] == 0
    assert tiles['registrado']['n'] == 0
    assert tiles['entregado']['n'] == 0
    assert tiles['otros']['n'] == 0
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
    # Las del fixture tienen más de 24 h: Nueva Ana y Luz van a Clientes sin estatus.
    lista = cliente('luz').get('/mis-asignaciones?e=nuevas').get_data(as_text=True)
    assert 'Nueva Ana' not in lista
    assert 'Sin estatus Luz' not in lista
    assert 'Contactada Miriam' not in lista
    sin_e = cliente('luz').get('/mis-asignaciones?e=sin_estatus').get_data(as_text=True)
    assert 'Sin estatus Luz' in sin_e
    assert 'Nueva Ana' in sin_e
    # Luz ve asignados de otras asesoras en el listado (admin).
    todos = cliente('jose').get('/mis-asignaciones?e=contactado').get_data(as_text=True)
    assert 'Contactada Miriam' in todos


def test_asesora_solo_las_suyas(monkeypatch):
    _carga(monkeypatch)
    html = cliente('ana').get('/mis-asignaciones?e=sin_estatus').get_data(as_text=True)
    assert 'Nueva Ana' in html
    assert 'Sin estatus Luz' not in html
    assert 'Contactada Miriam' not in html
    # Corte: la asignación del 30 oct no aparece.
    assert 'Anterior Ana' not in html
    # Perdida tampoco.
    perd = cliente('ana').get('/mis-asignaciones?e=busqueda').get_data(as_text=True)
    assert 'Busca Ana' in perd
    assert 'Espera Ana' in perd
    assert 'Perdida Ana' not in perd


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
    # Ana vigentes: Nueva (≥24 h → sin_estatus), Busca, Espera, Agendada, Oferta, Perdida.
    assert nums('ana') == [0, 1, 0, 2, 1, 1, 0, 0, 0, 0, 0, 1]
    # José y Luz: nuevas 0, sin_estatus 2 (Ana+Luz), contactado 1 (Miriam),
    # busqueda 2 (Busca+Espera), agendado 1, oferta 1, perdidos 1 (Ana)
    assert nums('jose') == [0, 2, 1, 2, 1, 1, 0, 0, 0, 0, 0, 1]
    assert nums('luz') == [0, 2, 1, 2, 1, 1, 0, 0, 0, 0, 0, 1]


def test_mercadeo_no_entra():
    assert cliente('mercadeo').get('/mis-asignaciones').status_code == 403
    assert cliente('mercadeo').get('/mis-asignaciones?e=nuevas').status_code == 403


def test_vista_tiene_fases_del_mockup(monkeypatch):
    _carga(monkeypatch)
    html = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    plano = html.replace('\u200b', '')
    for texto in ('1. Nuevas Asignaciones', 'Clientes sin estatus', '2. En Proceso', '3. En Negociación',
                  '4. Entrega de Inmueble', 'Perdidos', 'Contactado', 'En Búsqueda/Esperando', 'Agendado/Visitado',
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
    regla = 'Nuevas Asignaciones son las de hace menos de 24 horas'
    for slug in ('jose', 'ana', 'luz'):
        html = cliente(slug).get('/mis-asignaciones').get_data(as_text=True)
        assert texto in html
        assert regla in html
        assert 'ASIGNADO, No contactado o el campo vacío no cuentan como avance' in html
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
    # Sin fecha, tanto «Sin estatus» como «Nuevo» van a Clientes sin estatus.
    assert portal_lib.tile_de_asignacion('Sin estatus', '', 'Sin estatus') == 'sin_estatus'
    assert portal_lib.tile_de_asignacion('Nuevo', '', 'Nuevo') == 'sin_estatus'
    # Perdido (es_perdido / estado=Perdido) no se cuela en En Búsqueda.
    assert portal_lib.tile_de_asignacion('Perdido', 'Buscando', 'Buscando') == 'perdidos'
    assert portal_lib.tile_de_asignacion('En proceso', 'Buscando', 'Buscando') == 'busqueda'
    html = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    bloque = html[html.find('class="embudo"'):]
    assert bloque.find('1. Nuevas Asignaciones') < bloque.find('Clientes sin estatus') < bloque.find('2. En Proceso')
    assert bloque.find('4. Entrega de Inmueble') < bloque.find('Perdidos')
    perd = cliente('jose').get('/mis-asignaciones?e=perdidos').get_data(as_text=True)
    assert 'Perdida Ana' in perd
    assert 'Busca Ana' not in perd
    assert 'Anterior Ana' not in perd


def test_cuadre_misasig_igual_mis_clientes(monkeypatch):
    """El total de Mis Asignaciones y el de Mis Clientes coinciden a nivel de datos."""
    import re
    import app as appmod
    _carga(monkeypatch, extras=[
        {'id': 'asig1', 'nombre': 'Asignado Luz', 'asesor': 'luz', 'estado': 'ASIGNADO',
         'situacion': '', 'estatus': 'ASIGNADO', 'asignado_at': '2026-11-14', 'operacion': 'Alquiler'},
        {'id': 'priv1', 'nombre': 'Privado Ana', 'asesor': 'ana', 'estado': 'Negociación',
         'situacion': 'Documento privado', 'estatus': 'Documento privado',
         'asignado_at': '2026-11-14', 'operacion': 'Venta'},
    ])
    for slug in ('jose', 'luz', 'ana'):
        user = portal_lib.user_by_username(slug)
        clientes, _ = appmod._clientes_ejercicio(user)
        r = portal_lib.resumen_asignaciones(clientes)
        f = portal_lib.resumen_fases(clientes)
        assert r['en_embudo'] == r['total'] == f['total'] == len(clientes), (slug, r, f['total'], len(clientes))
        html_cli = cliente(slug).get('/mis-clientes').get_data(as_text=True)
        html_asig = cliente(slug).get('/mis-asignaciones').get_data(as_text=True)
        nums = [int(x) for x in re.findall(r'class="embudo-n">(\d+)</span>', html_asig)]
        assert nums and sum(nums) == r['total'], (slug, nums, r['total'])
        assert html_cli.count('class="cli-row"') == r['total'], (slug, html_cli.count('class="cli-row"'), r['total'])
        assert 'Anterior Ana' not in html_cli and 'Anterior Ana' not in html_asig
    # El estatus no mapeado aparece en Otros, visible y tocable.
    html_jose = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    assert 'href="/mis-asignaciones?e=otros"' in html_jose
    lista = cliente('jose').get('/mis-asignaciones?e=otros').get_data(as_text=True)
    assert 'Privado Ana' in lista
    # ASIGNADO del 14 nov ya superó las 24 h: va a Sin estatus, no a Nuevas.
    nuevas = cliente('jose').get('/mis-asignaciones?e=nuevas').get_data(as_text=True)
    assert 'Asignado Luz' not in nuevas
    assert 'Privado Ana' not in nuevas
    sin_e = cliente('jose').get('/mis-asignaciones?e=sin_estatus').get_data(as_text=True)
    assert 'Asignado Luz' in sin_e
    assert 'Asignado Luz' in cliente('jose').get('/mis-clientes').get_data(as_text=True)


def test_mis_clientes_sin_tableros_y_con_info(monkeypatch):
    """Mis Clientes ya no muestra las tarjetas de resumen; el texto va en la i."""
    _carga(monkeypatch)
    html = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    assert 'fase-kpis' not in html
    assert 'kpi fase-pct' not in html
    assert 'Con estatus' not in html
    assert 'fase-corte' not in html
    assert 'class="asig-info-btn"' in html
    assert 'popovertarget="cli-info"' in html
    assert 'id="cli-info"' in html
    assert 'Información de Mis Clientes' in html
    pop = html[html.find('id="cli-info"'):]
    assert 'en total' in pop
    assert 'Actualizado:' in pop
    assert 'Sistema de Clientes' in pop
    assert '31 oct 2026' in pop
    assert 'class="cli-row"' in html
    assert 'Nueva Ana' in html
    # El ícono va junto al título.
    tit = html.find('class="page-row asig-titulo"')
    info = html.find('class="asig-info-btn"')
    h1 = html.find('>Mis Clientes</h1>')
    assert tit != -1 and info != -1 and h1 != -1
    assert tit < h1 < info
    # Mis Asignaciones no se toca.
    asig = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    assert 'class="embudo"' in asig and '1. Nuevas Asignaciones' in asig



def test_mis_clientes_listas_contraidas_y_nuevas(monkeypatch):
    """Mis Clientes arranca con las listas cerradas, en el orden del embudo, e incluye Nuevas."""
    import re
    _carga(monkeypatch, extras=[
        {'id': 'nueva1', 'nombre': 'Reciente Luz', 'asesor': 'luz', 'estado': 'ASIGNADO',
         'situacion': '', 'estatus': 'ASIGNADO', 'asignado_at': '2099-01-01T12:00:00', 'operacion': 'Alquiler'},
    ])
    html = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    asig = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    # Todas las listas salen cerradas.
    for m in re.finditer(r'<details\b[^>]*class="[^"]*fase-sec[^"]*"[^>]*>', html):
        tag = m.group(0)
        assert ' open' not in tag, tag
    # Orden y nombres.
    ids = re.findall(r'id="fase-([a-z0-9_]+)"', html)
    esperados = ['nuevas', 'sin_estatus', 'contactado', 'f2', 'f3', 'f4', 'f5', 'perdidos']
    assert [i for i in ids if i in esperados] == esperados
    assert 'Fase 1 · Asignación y contacto' not in html
    # Los encabezados ya no llevan el prefijo «Fase N ·».
    import re as _re
    visibles = ' '.join(_re.findall(r'<summary>(.*?)</summary>', html, flags=_re.S))
    assert 'Fase' not in visibles
    assert 'Nuevas Asignaciones' in html
    assert 'Clientes sin estatus' in html
    assert 'Contactado' in html
    # Conteos iguales a los del embudo (Fase 4 y 5 suman sus baldosas).
    def n_grupo(hid, titulo):
        m = re.search(r'id="fase-%s"[^>]*>\s*<summary>\s*%s\s*<span class="muted">\((\d+)\)</span>' % (hid, re.escape(titulo)), html)
        assert m, (hid, titulo)
        return int(m.group(1))
    tiles = [int(x) for x in re.findall(r'class="embudo-n">(\d+)</span>', asig)]
    # nuevas, sin_estatus, contactado, busqueda, agendado, oferta, oferta_aceptada,
    # reserva_agendada, reserva_firmada, registrado, entregado, perdidos
    assert n_grupo('nuevas', 'Nuevas Asignaciones') == tiles[0]
    assert n_grupo('sin_estatus', 'Clientes sin estatus') == tiles[1]
    assert n_grupo('contactado', 'Contactado') == tiles[2]
    assert n_grupo('f2', 'Búsqueda / En proceso') == tiles[3]
    assert n_grupo('f3', 'Visitas') == tiles[4]
    assert n_grupo('f4', 'Negociación') == tiles[5] + tiles[6] + tiles[7]
    assert n_grupo('f5', 'Entrega') == tiles[8] + tiles[9] + tiles[10]
    assert n_grupo('perdidos', 'Perdidos') == tiles[11]
    suma = (tiles[0] + tiles[1] + tiles[2] + tiles[3] + tiles[4]
            + tiles[5] + tiles[6] + tiles[7] + tiles[8] + tiles[9] + tiles[10] + tiles[11])
    # Si aparece Otros, también suma.
    m_otros = re.search(r'id="fase-otros"[^>]*>\s*<summary>\s*Otros\s*<span class="muted">\((\d+)\)</span>', html)
    if m_otros:
        suma += int(m_otros.group(1))
    assert html.count('class="cli-row"') == suma
    assert 'Reciente Luz' in html
    # La fila de nuevas está dentro de su lista, no en Sin estatus.
    bloque_n = html[html.find('id="fase-nuevas"'):html.find('id="fase-sin_estatus"')]
    assert 'Reciente Luz' in bloque_n


def test_nada_se_pierde_y_perdidos_no_se_duplican():
    clientes = [
        {'estado': 'Nuevo', 'situacion': '', 'estatus': 'Nuevo'},
        {'estado': 'ASIGNADO', 'situacion': '', 'estatus': 'ASIGNADO'},
        {'estado': 'Sin estatus', 'situacion': '', 'estatus': 'Sin estatus'},
        {'estado': 'En proceso', 'situacion': 'Buscando', 'estatus': 'Buscando'},
        {'estado': 'En proceso', 'situacion': 'Esperando', 'estatus': 'Esperando'},
        {'estado': 'En proceso', 'situacion': 'Visita realizada', 'estatus': 'Visita realizada'},
        {'estado': 'Perdido', 'situacion': 'Esperando', 'estatus': 'Esperando'},
        {'estado': 'Perdido', 'situacion': 'Buscando', 'estatus': 'Buscando'},
        {'estado': 'Negociación', 'situacion': 'Documento privado', 'estatus': 'Documento privado'},
        {'estado': 'En proceso', 'situacion': '', 'estatus': 'En proceso'},
        {'estado': '', 'situacion': '', 'estatus': 'Algo raro'},
    ]
    r = portal_lib.resumen_asignaciones(clientes)
    f = portal_lib.resumen_fases(clientes)
    assert r['en_embudo'] == r['total'] == f['total'] == len(clientes)
    assert r['tiles']['nuevas']['n'] == 0  # sin fecha no entra en Nuevas
    assert r['tiles']['sin_estatus']['n'] == 3  # Nuevo + ASIGNADO + Sin estatus
    assert r['tiles']['busqueda']['n'] == 2  # Buscando + Esperando
    assert r['tiles']['agendado']['n'] == 1  # Visita realizada
    assert r['tiles']['perdidos']['n'] == 2  # no se suman también a Búsqueda
    assert r['tiles']['otros']['n'] == 3  # Documento privado, En proceso, Algo raro
    for c in clientes:
        assert portal_lib.tile_de_asignacion(
            c['estado'], c['situacion'], c['estatus']) is not None


def test_regla_24h():
    """Nuevas: menos de 24 h y sin avance. Sin estatus: 24 h o más, o sin fecha."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ahora = datetime(2026, 11, 22, 21, 0, tzinfo=ZoneInfo('America/Caracas'))
    clientes = [
        {'nombre': 'Reciente ASIGNADO', 'estado': 'ASIGNADO', 'situacion': '', 'estatus': 'ASIGNADO',
         'asignado_at': '2026-11-22T10:00:00'},  # 11 h
        {'nombre': 'Reciente vacío', 'estado': '', 'situacion': '', 'estatus': '',
         'asignado_at': '2026-11-21T21:00:01'},  # 23 h 59 m
        {'nombre': 'Justo 24 h', 'estado': 'No contactado', 'situacion': '', 'estatus': 'No contactado',
         'asignado_at': '2026-11-21T21:00:00'},  # exactamente 24 h
        {'nombre': 'Viejo Sin estatus', 'estado': 'Sin estatus', 'situacion': '', 'estatus': 'Sin estatus',
         'asignado_at': '2026-11-10T08:00:00'},
        {'nombre': 'Sin fecha', 'estado': 'Nuevo', 'situacion': '', 'estatus': 'Nuevo', 'asignado_at': ''},
        {'nombre': 'Contactado reciente', 'estado': 'Nuevo', 'situacion': 'Contactado', 'estatus': 'Contactado',
         'asignado_at': '2026-11-22T10:00:00'},
        {'nombre': 'Perdido reciente', 'estado': 'Perdido', 'situacion': 'Esperando', 'estatus': 'Esperando',
         'asignado_at': '2026-11-22T10:00:00'},
    ]
    r = portal_lib.resumen_asignaciones(clientes, now=ahora)
    f = portal_lib.resumen_fases(clientes, now=ahora)
    assert r['en_embudo'] == r['total'] == f['total'] == len(clientes)
    nombres = lambda tid: [c['nombre'] for c in r['tiles'][tid]['clientes']]
    assert nombres('nuevas') == ['Reciente ASIGNADO', 'Reciente vacío']
    assert nombres('sin_estatus') == ['Justo 24 h', 'Viejo Sin estatus', 'Sin fecha']
    assert nombres('contactado') == ['Contactado reciente']
    assert nombres('perdidos') == ['Perdido reciente']
    # La Fase 1 de Mis Clientes usa las mismas etiquetas y no pierde a nadie.
    f1 = next(g for g in f['grupos'] if g['id'] == 'f1')
    des = dict(f1['desglose'])
    assert des.get('Nuevas Asignaciones') == 2
    assert des.get('Clientes sin estatus') == 3
    assert des.get('Contactado') == 1
    assert f1['total'] == 6


def test_sello_actualizado_en_vivo_no_dice_copia(monkeypatch):
    _carga(monkeypatch)
    html_a = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    html_c = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    sello = portal_lib.fmt_fecha_hora('2026-11-16T12:00:00-04:00')
    assert 'Copia del' not in html_a
    assert 'Actualizado: ' + sello in html_a
    assert 'Copia del' not in html_c
    assert 'Actualizado: ' + sello in html_c


def test_sello_usa_hora_de_sync_aunque_no_haya_cambios(monkeypatch):
    """El sello muestra la última sincronización exitosa, aunque no haya habido cambios."""
    import clientes_sync
    _carga(monkeypatch)
    iso = '2026-11-22T22:43:09-04:00'
    clientes_sync._ultimo = {'ok': True, 'actualizado': iso, 'n': 209, 'cambiados': 0}
    sello = portal_lib.fmt_fecha_hora(iso)
    html_a = cliente('jose').get('/mis-asignaciones').get_data(as_text=True)
    html_c = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    assert 'Actualizado: ' + sello in html_a
    assert 'Actualizado: ' + sello in html_c
    viejo = portal_lib.fmt_fecha_hora('2026-11-16T12:00:00-04:00')
    assert 'Actualizado: ' + viejo not in html_a
    assert 'Actualizado: ' + viejo not in html_c
    clientes_sync.reset_memoria()


def test_fmt_fecha_hora_caracas():
    s = portal_lib.fmt_fecha_hora('2026-11-22T18:23:54-05:00')
    # 18:23 en UTC-5 = 19:23 en Caracas (UTC-4).
    assert s == '22 de noviembre de 2026, 7:23 p. m. (Caracas)'
    assert portal_lib.fmt_fecha_hora('') == 'fecha no disponible'
