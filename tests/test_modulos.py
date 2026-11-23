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
                 '/mi-equipo', '/mis-captaciones', '/mercadeo', '/mercadeo/planificador',
                 '/ofertas', '/cierres', '/admin/actividades', '/clientes-propios', '/visitas'):
        assert f'href="{href}"' not in nav_j, href
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
    assert 'Cerrar sesión' in sheet_a
    # Asesora: solo perfil (y Cerrar sesión).
    nav_h = _bottomnav(cliente('herlinda').get('/modulos').get_data(as_text=True))
    sheet_h = _sheet(cliente('herlinda').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones',
                 '/mis-inmuebles', '/ofertas', '/cierres', '/actividades', '/mercadeo'):
        assert f'href="{href}"' not in nav_h, href
    assert 'href="/perfil"' in sheet_h
    assert 'href="/aprobaciones"' not in sheet_h
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
    # Planificador solo para Administrador.
    assert '/mercadeo/planificador' not in main
    assert '/mercadeo/planificador' in cliente('jose').get('/modulos').get_data(as_text=True)


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
    """Mis Captaciones y Captaciones For Rent aparecen una sola vez, con Inmuebles y Mercadeo dentro."""
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
        assert labels[:3] == ['Mis Captaciones', 'Captaciones For Rent', 'Mercadeo'], (slug, labels)
        for titulo, cuerpo in grupos[:2]:
            assert re.search(r'<h3>Inmuebles</h3>', cuerpo), titulo
            assert re.search(r'<h3>Mercadeo', cuerpo), titulo
        mc = grupos[0][1]
        assert 'href="/mis-captaciones"' in mc and 'href="/mercadeo"' in mc
        assert 'Propios, Alianza y Todos.' in mc
        fr = grupos[1][1]
        assert 'href="/inventario"' in fr
        if slug == 'ana':
            assert 'En preparación' in fr and not re.search(r'href="/mercadeo"', fr)
        else:
            assert 'href="/mercadeo"' in fr
        mer = dict(grupos)['Mercadeo']
        assert re.search(r'<h3>Estadísticas', mer) and 'En preparación' in mer
        assert 'Estadísticas de Mercadeo' not in mer
        assert 'Llega en la etapa 8, solo con datos reales.' in mer
        assert 'Cargar inmueble' not in main
        assert 'href="/mis-inmuebles"' not in main
        if slug == 'jose':
            assert re.search(r'<h3>Planificador</h3>', mer)
            assert 'href="/mercadeo/planificador"' in mer and 'Solo Administrador.' in mer
            assert 'Por revisar (carga)' in main
        else:
            assert 'Planificador' not in mer
            assert 'href="/mercadeo/planificador"' not in main

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
    h = cliente('jose').get('/mercadeo').get_data(as_text=True)
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
    for path in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones', '/ofertas', '/perfil', '/mercadeo'):
        h = cliente('ana').get(path).get_data(as_text=True)
        m = _atras(h)
        assert m and m.group(1) == '/modulos', path
    h = cliente('jose').get('/mercadeo').get_data(as_text=True)
    assert _atras(h) and _atras(h).group(1) == '/modulos'
    # Mercadeo: su inicio y su planificador (raíces de su barra) no lo duplican.
    assert 'btn-atras' not in cliente('mercadeo').get('/mercadeo').get_data(as_text=True)


def test_cargar_inmueble_en_mis_captaciones():
    """La carga se abre desde Mis Captaciones › Inmuebles, no como entrada suelta en Módulos."""
    for slug in ('jose', 'ana'):
        h = cliente(slug).get('/mis-captaciones').get_data(as_text=True)
        m = re.search(r'<a class="btn-carga" href="([^"]+)">([^<]+)</a>', h)
        assert m and m.group(1) == '/mis-inmuebles' and m.group(2) == 'Cargar inmueble', slug
