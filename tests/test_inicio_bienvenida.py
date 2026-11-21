"""Inicio de Presidencia: pantalla vacía con bienvenida. Otros roles no cambian."""
import re

from test_bandeja import cliente  # noqa: F401  (configura el entorno de prueba)


def _main(html):
    m = re.search(r'<main\b.*?</main>', html, re.S)
    assert m, 'falta <main>'
    return m.group(0)


def _bottomnav(html):
    m = re.search(r'<nav class="bottomnav".*?</nav>', html, re.S)
    assert m, 'falta la barra inferior'
    return m.group(0)


def _botones(nav):
    sheet = re.sub(r'<div class="sheet".*?</div>', '', nav, flags=re.S)
    return re.findall(r'<span>([^<]+)</span>', sheet)


def test_inicio_presidencia_solo_bienvenida():
    r = cliente('jose').get('/portal')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    main = _main(html)
    assert '<em>José</em>, bienvenido a For Rent Online de For Rent Grupo Inmobiliario, C.A.' in main
    assert 'RIF J-50475755-1' in main
    assert 'Bienvenido, <em>José</em>' not in main
    assert 'For Rent Online · For Rent Grupo Inmobiliario' not in main
    assert 'Resumen de operación' not in main
    assert 'Mis Asignaciones' not in main
    assert 'Clientes por fase' not in main
    assert 'Accesos' not in main
    assert 'Sistema de Clientes' not in main
    # Encabezado y barra inferior se mantienen.
    assert 'Hola, José' in html
    assert 'class="logo"' in html
    assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más']
    # Sin botón atrás en Inicio.
    assert 'btn-atras' not in html


def test_luz_admin_tambien_ve_bienvenida():
    """Luz también es admin/Presidencia: mismo inicio, con su nombre."""
    html = cliente('luz').get('/').get_data(as_text=True)
    main = _main(html)
    assert '<em>Luz</em>, bienvenida a For Rent Online de For Rent Grupo Inmobiliario, C.A.' in main
    assert 'RIF J-50475755-1' in main
    assert 'Bienvenido, <em>Luz</em>' not in main
    assert 'bienvenido a For Rent Online' not in main
    assert 'For Rent Online · For Rent Grupo Inmobiliario' not in main
    assert 'Mis Asignaciones' not in main
    assert 'Hola, Luz' in html


def test_cifras_siguen_en_modulos_y_rutas():
    mods = cliente('jose').get('/modulos').get_data(as_text=True)
    assert 'Mis Asignaciones' in mods
    assert 'Mis Clientes (por fases)' in mods
    assert cliente('jose').get('/mis-asignaciones').status_code == 200
    assert cliente('jose').get('/mis-clientes').status_code == 200
    cli = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    assert 'Clientes por fase' in cli or 'fase' in cli.lower()


def test_inicio_lider_y_asesora_no_cambia():
    for slug, nombre in (('ana', 'Ana'), ('herlinda', 'Herlinda')):
        html = cliente(slug).get('/').get_data(as_text=True)
        main = _main(html)
        assert 'Tu resumen' in main, slug
        assert 'Mis Asignaciones' in main, slug
        assert 'Bienvenido,' not in main, slug
        assert f'Hola, {nombre}' in html, slug
        assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más'], slug
