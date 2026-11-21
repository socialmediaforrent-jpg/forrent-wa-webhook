"""Inicio de Presidencia: pantalla vacía con bienvenida. Otros roles no cambian."""
import os
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
    assert '<em>José</em>, bienvenido a For Rent Online.' in main
    assert 'de For Rent Grupo Inmobiliario' not in main
    assert 'RIF J-50475755-1' not in main
    assert 'Bienvenido, <em>José</em>' not in main
    assert 'For Rent Online · For Rent Grupo Inmobiliario' not in main
    assert 'inicio-equipo.webp' in main
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
    assert '<em>Luz</em>, bienvenida a For Rent Online.' in main
    assert 'de For Rent Grupo Inmobiliario' not in main
    assert 'RIF J-50475755-1' not in main
    assert 'Bienvenido, <em>Luz</em>' not in main
    assert 'bienvenido a For Rent Online' not in main
    assert 'For Rent Online · For Rent Grupo Inmobiliario' not in main
    assert 'inicio-equipo.webp' in main
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
        assert 'inicio-equipo.webp' not in main, slug
        assert 'bienvenido a For Rent Online' not in main, slug
        assert f'Hola, {nombre}' in html, slug
        assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más'], slug


def test_foto_equipo_se_sirve():
    """La foto del equipo se sirve desde la app y solo aparece en el inicio de Presidencia."""
    r = cliente('jose').get('/static/inicio-equipo.webp')
    assert r.status_code == 200
    assert r.mimetype == 'image/webp'
    assert 0 < len(r.get_data()) <= 250_000


def test_css_saludo_queda_encima_de_la_barra():
    """El saludo reserva la barra inferior fija y un margen sobre la mesa."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'app.css')
    css = open(ruta, encoding='utf-8').read()
    m = re.search(r'\.welcome-inicio\{[^}]+\}', css)
    assert m, 'falta la regla .welcome-inicio'
    bloque = m.group(0)
    assert '100dvh' in bloque
    assert 'var(--bottom)' in bloque
    assert 'safe-area-inset-bottom' in bloque
    assert '22dvh' in bloque
    # El padding fijo de 36px dejaba el título detrás de los botones en Android.
    assert 'padding:24px 24px 36px' not in bloque
    assert 'body:has(> main.welcome-inicio){padding-bottom:0}' in css
