"""Inicio: la foto del equipo y el saludo para todos; debajo, su resumen (si lo hay)."""
import os
import re

from test_bandeja import cliente  # noqa: F401
os.environ.setdefault('INVENTARIO_PASSWORD', 'clave-inv')


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
    assert 'caracas-hero.webp' in main
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
    assert 'caracas-hero.webp' in main
    assert 'Mis Asignaciones' not in main
    assert 'Hola, Luz' in html


def test_cifras_siguen_en_modulos_y_rutas():
    mods = cliente('jose').get('/modulos').get_data(as_text=True)
    assert 'Mis Asignaciones' not in mods
    assert 'Mis Clientes' in mods
    assert cliente('jose').get('/mis-clientes').status_code == 200
    assert cliente('jose').get('/mis-clientes').status_code == 200
    cli = cliente('jose').get('/mis-clientes').get_data(as_text=True)
    assert 'Clientes por fase' in cli or 'fase' in cli.lower()


def test_inicio_lider_y_asesora_tienen_bienvenida_y_resumen():
    for slug, nombre, saludo in (('ana', 'Ana', 'bienvenida'), ('herlinda', 'Herlinda', 'bienvenida'),
                                 ('antonio', 'Antonio', 'bienvenido')):
        html = cliente(slug).get('/').get_data(as_text=True)
        main = _main(html)
        assert f'<em>{nombre}</em>, {saludo} a For Rent Online.' in main, slug
        assert 'caracas-hero.webp' in main, slug
        # El resumen sigue debajo del encabezado.
        assert main.find('caracas-hero.webp') < main.find('Tu resumen'), slug
        assert 'Tu resumen' in main, slug
        assert 'Mis Clientes' in main and 'Mis Asignaciones' not in main, slug
        assert 'Clientes por fase' not in main, slug
        assert f'Hola, {nombre}' in html, slug
        assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más'], slug


def test_foto_equipo_se_sirve():
    """La foto del equipo sigue en estáticos (por si se reutiliza)."""
    r = cliente('jose').get('/static/inicio-equipo.webp')
    assert r.status_code == 200
    assert r.mimetype == 'image/webp'
    assert 0 < len(r.get_data()) <= 250_000


def test_foto_caracas_se_sirve():
    """La foto de Caracas del encabezado se sirve y pesa poco."""
    r = cliente('jose').get('/static/caracas-hero.webp')
    assert r.status_code == 200
    assert r.mimetype == 'image/webp'
    data = r.get_data()
    assert 0 < len(data) <= 250_000


def test_css_saludo_queda_encima_de_la_barra():
    """El saludo de Presidencia reserva la barra inferior fija y el safe-area."""
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'app.css')
    css = open(ruta, encoding='utf-8').read()
    assert 'body:has(> main.welcome-only){padding-bottom:0}' in css
    assert '.welcome-only .cool-hero' in css or '.cool-hero-full' in css
    assert '100dvh' in css
    assert 'safe-area-inset-bottom' in css
    assert 'var(--bottom)' in css


def test_inicio_mercadeo_e_inventario_tambien_saludan():
    html = cliente('mercadeo').get('/').get_data(as_text=True)
    main = _main(html)
    assert '<em>Mercadeo</em>, bienvenido a For Rent Online.' in main
    assert 'caracas-hero.webp' in main
    assert main.find('caracas-hero.webp') < main.find('Tu resumen')
    assert 'href="/mercadeo"' in main
    assert _botones(_bottomnav(html)) == ['Inicio', 'Mercadeo', 'Planificador', 'Más']

    html = cliente('inventario').get('/portal').get_data(as_text=True)
    main = _main(html)
    assert '<em>Inventario</em>, bienvenido a For Rent Online.' in main
    assert 'caracas-hero.webp' in main
    assert 'Tu resumen' in main
    assert 'href="/inventario"' in main
    assert _botones(_bottomnav(html)) == ['Inicio', 'Inmuebles', 'Por revisar', 'Más']
