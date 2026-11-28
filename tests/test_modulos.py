"""Etapa 1 (Prompt Maestro): barra de 4 botones e índice de 5 módulos, sin números inventados ni permisos nuevos."""
import re

from test_bandeja import cliente, appmod  # noqa: F401  (configura el entorno de prueba)


def _bottomnav(html):
    m = re.search(r'<nav class="bottomnav".*?</nav>', html, re.S)
    assert m, 'falta la barra inferior'
    return m.group(0)


def _botones(nav):
    sheet = re.sub(r'<div class="sheet".*?</div>', '', nav, flags=re.S)
    return re.findall(r'<span>([^<]+)</span>', sheet)


def test_barra_cuatro_botones():
    for slug in ('jose', 'ana', 'herlinda', 'antonio'):
        r = cliente(slug).get('/modulos')
        assert r.status_code == 200, slug
        assert _botones(_bottomnav(r.get_data(as_text=True))) == ['Inicio', 'Módulos', 'Mensajes', 'Más'], slug


def _sheet(html):
    nav = _bottomnav(html)
    m = re.search(r'<div class="sheet".*?</div>', nav, re.S)
    assert m, 'falta la hoja de Más'
    return m.group(0)


def test_mas_sin_redundancia_con_modulos():
    """Más no repite lo que ya está en Módulos. El perfil y Cerrar sesión se quedan."""
    # Presidencia: lo que ya vive en Módulos sale de Más.
    nav_j = _bottomnav(cliente('jose').get('/modulos').get_data(as_text=True))
    sheet_j = _sheet(cliente('jose').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/inventario', '/mis-inmuebles', '/inventario/cola',
                 '/mi-equipo', '/mis-captaciones', '/mercadeo',
                 '/ofertas', '/cierres', '/admin/actividades', '/clientes-propios', '/visitas'):
        assert f'href="{href}"' not in nav_j, href
    # Planificador solo en Más, para rol admin (José y Luz). No se repite en Módulos.
    assert 'href="/mercadeo/planificador"' in sheet_j
    assert 'href="/mercadeo/planificador"' in _sheet(cliente('luz').get('/modulos').get_data(as_text=True))
    assert 'href="/aprobaciones"' in sheet_j
    assert 'href="/admin/correos"' in sheet_j
    assert 'Cerrar sesión' in sheet_j
    # Si hay URL de los sistemas externos, siguen en Más (no están en Módulos).
    # Si no hay URL, simplemente no aparecen.
    # Líder: perfil y aprobaciones se quedan; lo demás de Módulos, no.
    nav_a = _bottomnav(cliente('ana').get('/modulos').get_data(as_text=True))
    sheet_a = _sheet(cliente('ana').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones',
                 '/mis-inmuebles', '/ofertas', '/cierres', '/actividades',
                 '/mi-equipo', '/clientes-propios', '/mercadeo'):
        assert f'href="{href}"' not in nav_a, href
    assert 'href="/perfil"' in sheet_a
    assert 'href="/aprobaciones"' in sheet_a
    assert 'href="/mercadeo/planificador"' not in sheet_a
    assert 'Cerrar sesión' in sheet_a
    # Asesora: solo perfil (y Cerrar sesión).
    nav_h = _bottomnav(cliente('herlinda').get('/modulos').get_data(as_text=True))
    sheet_h = _sheet(cliente('herlinda').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones',
                 '/mis-inmuebles', '/ofertas', '/cierres', '/actividades', '/mercadeo'):
        assert f'href="{href}"' not in nav_h, href
    assert 'href="/perfil"' in sheet_h
    assert 'href="/aprobaciones"' not in sheet_h
    assert 'href="/mercadeo/planificador"' not in sheet_h
    assert 'Cerrar sesión' in sheet_h
    # Las rutas siguen existiendo; solo se quitó la entrada duplicada del menú.
    assert cliente('jose').get('/mis-clientes').status_code == 200
    assert cliente('jose').get('/aprobaciones').status_code == 200
    assert cliente('ana').get('/mis-captaciones').status_code == 200
    assert cliente('herlinda').get('/perfil').status_code == 200


def test_cinco_modulos_sin_numeros():
    h = cliente('ana').get('/modulos').get_data(as_text=True)
    for t in ('1. Inventario', '2. Clientes', '3. Operaciones', '4. Comisiones', '5. Rendimiento'):
        assert t in h, t
    main = re.search(r'<main.*?</main>', h, re.S).group(0)
    # Las secciones en preparación no muestran cifras.
    for card in re.findall(r'<div class="(?:card |mod-sub )?mod-prox".*?</div>', main, re.S):
        assert not re.search(r'\d+\s*(%|US\$|clientes|inmuebles)', card), card
    assert 'En preparación' in main
    # Planificador sale de Módulos (vive en Más, solo admin).
    assert '/mercadeo/planificador' not in main
    main_j = re.search(r'<main.*?</main>', cliente('jose').get('/modulos').get_data(as_text=True), re.S).group(0)
    assert '/mercadeo/planificador' not in main_j


def test_modulos_salen_contraidos():
    """Al abrir Módulos, los 5 salen cerrados (solo título y chevron); se abren al tocarlos."""
    esperados = ['inventario', 'clientes', 'operaciones', 'comisiones', 'rendimiento']
    for slug in ('jose', 'ana', 'herlinda'):
        h = cliente(slug).get('/modulos').get_data(as_text=True)
        bloques = re.findall(r'<details class="sec mod"[^>]*>', h)
        assert len(bloques) == 5, (slug, bloques)
        keys = re.findall(r'<details class="sec mod" id="mod-([^"]+)"\s*>', h)
        assert keys == esperados, (slug, keys)
        for tag in bloques:
            assert not re.search(r'(?:^|\s)open(?:\s|=|/|>|$)', tag), (slug, tag)
        # Esta pantalla no abre un módulo por hash ni guarda cuál quedó abierto.
        assert 'localStorage' not in h
        assert 'sessionStorage' not in h
        assert 'location.hash' not in h




def test_captaciones_agrupadas():
    """Inventario: Mis Captaciones y Captaciones For Rent, cada una con Inmuebles y Estadísticas."""
    for slug in ('jose', 'ana'):
        h = cliente(slug).get('/modulos').get_data(as_text=True)
        main = re.search(r'<main.*?</main>', h, re.S).group(0)
        assert main.count('Mis Captaciones') == 1, slug
        assert main.count('Captaciones For Rent') == 1, slug
        assert 'Mis Captaciones ›' not in main and 'Captaciones For Rent ›' not in main
        assert not re.search(r'<details class="card mod-group"[^>]*\sopen', main)
        grupos = re.findall(
            r'<details class="card mod-group">\s*<summary class="mod-group-sum">\s*<span>\s*<h2>([^<]+)</h2>(.*?)</details>',
            main, re.S)
        labels = [g[0] for g in grupos]
        assert labels[:2] == ['Mis Captaciones', 'Captaciones For Rent'], (slug, labels)
        assert 'Mercadeo' not in labels
        for titulo, cuerpo in grupos[:2]:
            assert re.search(r'<h3>Inmuebles</h3>', cuerpo), titulo
            assert not re.search(r'<h3>Mercadeo', cuerpo), titulo
            assert re.search(r'<h3>Estadísticas', cuerpo), titulo
            assert 'En preparación' in cuerpo
            assert 'Llega en la etapa 8, solo con datos reales.' in cuerpo
        mc = grupos[0][1]
        assert 'href="/mis-captaciones"' in mc
        assert 'href="/mercadeo"' not in mc
        assert 'href="/mercadeo/forrent"' not in mc
        assert 'Propios, Alianza y Todos.' in mc
        fr = grupos[1][1]
        assert 'href="/inventario"' in fr
        assert not re.search(r'href="/mercadeo', fr)
        assert 'Cargar inmueble' not in main
        assert 'href="/mis-inmuebles"' not in main
        assert 'href="/mercadeo/planificador"' not in main
        assert 'Planificador' not in main
        if slug == 'jose':
            assert 'Por revisar (carga)' in main
        else:
            assert 'Por revisar (carga)' not in main

def test_permisos_sin_cambios():
    assert cliente('mercadeo').get('/modulos').status_code == 403
    assert cliente('inventario').get('/modulos').status_code in (302, 403)  # nunca 200
    r = appmod.app.test_client().get('/modulos')
    assert r.status_code in (302, 303) and '/login' in r.headers['Location']
    # Una asesora sigue sin entrar al planificador ni a pantallas de admin.
    assert cliente('ana').get('/mercadeo/planificador').status_code == 403
    assert cliente('ana').get('/admin/actividades').status_code == 403


def test_pestanas_propios_alianza_todos():
    h = cliente('ana').get('/mis-captaciones').get_data(as_text=True)
    assert re.search(r'Propios \(\d+\).*Alianza \(\d+\).*Todos \(\d+\)', h, re.S)


def _atras(html):
    return re.search(r'class="btn-atras"[^>]*href="([^"]+)"', html)


def test_boton_atras():
    """El encabezado muestra Atrás (ícono SVG + texto) fuera de Inicio y Mensajes, con fallback al padre."""
    # /modulos lo tiene (José lo pidió al entrar ahí); el fallback es Inicio.
    h = cliente('ana').get('/modulos').get_data(as_text=True)
    m = _atras(h)
    assert m and m.group(1) == '/portal'
    btn = re.search(r'<a class="btn-atras"[^>]*>.*?</a>', h, re.S)
    assert btn and '<svg' in btn.group(0) and '>Atrás</span>' in btn.group(0) and '←' not in btn.group(0)
    h = cliente('jose').get('/modulos').get_data(as_text=True)
    assert _atras(h) and _atras(h).group(1) == '/portal'
    h = cliente('herlinda').get('/modulos').get_data(as_text=True)
    assert _atras(h) and _atras(h).group(1) == '/portal'
    # Raíces de la barra: Inicio y Mensajes no lo tienen.
    for slug, path in (('ana', '/portal'), ('ana', '/panel'), ('jose', '/portal'), ('jose', '/panel')):
        assert 'btn-atras' not in cliente(slug).get(path).get_data(as_text=True), (slug, path)
    # Pantallas de módulos / Más: sí, y caen a /modulos si no hay historial.
    for path in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones', '/ofertas', '/perfil'):
        h = cliente('ana').get(path).get_data(as_text=True)
        m = _atras(h)
        assert m and m.group(1) == '/modulos', path
    # La vieja ruta /mercadeo redirige a la lista de inmuebles (sin links rotos).
    r = cliente('ana').get('/mercadeo')
    assert r.status_code in (302, 303) and r.headers['Location'].endswith('/inventario')
    r = cliente('jose').get('/mercadeo/forrent')
    assert r.status_code in (302, 303) and r.headers['Location'].endswith('/inventario')
    # Rol mercadeo: Inmuebles y el planificador son raíces de su barra (sin Atrás duplicado).
    assert 'btn-atras' not in cliente('mercadeo').get('/inventario').get_data(as_text=True)
    assert 'btn-atras' not in cliente('mercadeo').get('/mercadeo/planificador').get_data(as_text=True)


def test_agregar_captacion_en_mis_captaciones():
    """Un solo botón «Agregar captación» en Mis Captaciones, no como entrada suelta en Módulos."""
    for slug in ('jose', 'ana'):
        h = cliente(slug).get('/mis-captaciones').get_data(as_text=True)
        m = re.search(r'<a class="btn-carga" href="([^"]+)">([^<]+)</a>', h)
        assert m and m.group(1) == '/mis-captaciones/agregar' and m.group(2) == 'Agregar captación', slug
        assert 'Cargar inmueble' not in h
        assert '+ Solicitar agregar' not in h
    h = cliente('ana').get('/mis-captaciones/agregar').get_data(as_text=True)
    assert '¿Es un inmueble nuevo o ya está en el inventario?' in h
    assert 'Es un inmueble nuevo' in h
    assert 'Ya está en el inventario' in h
    assert 'action="/mis-inmuebles/nuevo"' in h
    h = cliente('ana').get('/mis-captaciones/agregar?tipo=existente').get_data(as_text=True)
    assert 'name="accion" value="alta"' in h
    assert 'Enviar solicitud' in h
    # José carga inmuebles nuevos, no pide que se los asignen.
    hj = cliente('jose').get('/mis-captaciones/agregar').get_data(as_text=True)
    assert 'Es un inmueble nuevo' in hj
    assert 'Ya está en el inventario' not in hj
    assert cliente('jose').get('/mis-captaciones/agregar?tipo=existente').status_code in (302, 303)

def test_mercadeo_mis_captaciones_solo_del_usuario():
    """Mis Captaciones › Mercadeo cuenta solo las captaciones del usuario conectado."""
    import app as appmod
    import portal_lib
    ana = portal_lib.user_by_username('ana')
    jose = portal_lib.user_by_username('jose')
    _, mias_ana, _ = appmod._mercadeo_items(ana, alcance='mias')
    _, fr_jose, _ = appmod._mercadeo_items(jose, alcance='forrent')
    _, mias_jose, _ = appmod._mercadeo_items(jose, alcance='mias')
    assert mias_ana and all(c.get('asesor') == 'ana' for c in mias_ana)
    assert len(mias_ana) == len(portal_lib.load_captaciones(slug='ana'))
    assert len(mias_jose) == 0
    assert len(fr_jose) > len(mias_ana)
    # La página-lista de Mercadeo ya no se sirve: ambas rutas van a Inmuebles.
    for slug, path in (('ana', '/mercadeo'), ('jose', '/mercadeo'), ('jose', '/mercadeo/forrent'),
                       ('ana', '/mercadeo/forrent')):
        r = cliente(slug).get(path)
        assert r.status_code in (302, 303), (slug, path)
        assert r.headers['Location'].endswith('/inventario'), (slug, path)


def test_mercadeo_info_y_sin_planificador():
    """El planificador sigue existiendo en su ruta; ya no hay entrada Mercadeo en Módulos."""
    r = cliente('jose').get('/mercadeo/forrent')
    assert r.status_code in (302, 303) and r.headers['Location'].endswith('/inventario')
    h = cliente('jose').get('/modulos').get_data(as_text=True)
    main = re.search(r'<main.*?</main>', h, re.S).group(0)
    assert 'href="/mercadeo"' not in main
    assert 'href="/mercadeo/forrent"' not in main
    assert 'Planificador de anuncios' not in main
    assert cliente('jose').get('/mercadeo/planificador').status_code == 200
