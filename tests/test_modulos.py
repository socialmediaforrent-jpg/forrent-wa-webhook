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


def test_nada_se_pierde_en_mas():
    nav = _bottomnav(cliente('ana').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/visitas', '/inventario', '/mis-captaciones', '/perfil', '/ofertas', '/cierres', '/actividades'):
        assert f'href="{href}"' in nav, href
    nav = _bottomnav(cliente('jose').get('/modulos').get_data(as_text=True))
    for href in ('/mis-clientes', '/inventario', '/mercadeo/planificador', '/admin/actividades', '/aprobaciones'):
        assert f'href="{href}"' in nav, href


def test_cinco_modulos_sin_numeros():
    h = cliente('ana').get('/modulos').get_data(as_text=True)
    for t in ('1. Inventario', '2. Clientes', '3. Operaciones', '4. Comisiones', '5. Rendimiento'):
        assert t in h, t
    main = re.search(r'<main.*?</main>', h, re.S).group(0)
    # Las secciones en preparación no muestran cifras.
    for card in re.findall(r'<div class="card mod-prox".*?</div>', main, re.S):
        assert not re.search(r'\d+\s*(%|US\$|clientes|inmuebles)', card), card
    assert 'En preparación' in main
    # Planificador solo para Administrador.
    assert '/mercadeo/planificador' not in main
    assert '/mercadeo/planificador' in cliente('jose').get('/modulos').get_data(as_text=True)


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
