"""Menú cool de Inicio y Mis Clientes: encabezado de Caracas, KPI y barra oscura."""
import os
import re

from test_bandeja import cliente  # noqa: F401
os.environ.setdefault('INVENTARIO_PASSWORD', 'clave-inv')


def _bottomnav(html):
    m = re.search(r'<nav class="bottomnav".*?</nav>', html, re.S)
    assert m, 'falta la barra inferior'
    return m.group(0)


def _botones(nav):
    sheet = re.sub(r'<div class="sheet".*?</div>', '', nav, flags=re.S)
    return re.findall(r'<span>([^<]+)</span>', sheet)


def test_mis_clientes_tiene_estilo_cool(monkeypatch):
    html = cliente('ana').get('/mis-clientes').get_data(as_text=True)
    assert 'class="theme-cool"' in html or 'theme-cool' in html
    assert 'caracas-hero.webp' in html
    assert 'logo-blanco.webp' in html
    assert 'Hola, Ana' in html
    assert 'Líder comercial' in html
    assert '>Mis Clientes</h1>' in html
    assert 'Embudo comercial de tus asignaciones' in html
    assert 'class="cool-kpis"' in html
    assert 'Nuevas Asignaciones' in html
    assert 'Clientes sin estatus' in html
    assert 'cool-kpi kpi-proceso' in html
    assert 'cool-kpi kpi-neg' in html
    assert 'class="cool-kpi-n">' in html
    assert 'embudo-flecha' in html
    assert 'data-embudo-tile="nuevas"' in html
    assert 'data-embudo-tile="sin_estatus"' in html
    assert 'data-embudo-kpi="nuevas"' in html
    assert 'Periodismodepaz' in html
    assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más']
    # Los conteos de baldosas no se duplican con los KPI.
    # (los KPI usan cool-kpi-n, no embudo-n)


def test_inicio_asesora_tiene_estilo_cool():
    html = cliente('ana').get('/portal').get_data(as_text=True)
    main = re.search(r'<main\b.*?</main>', html, re.S).group(0)
    assert 'theme-cool' in html
    assert 'inicio-equipo.webp' in html
    assert 'logo-blanco.webp' in html
    assert 'Hola, Ana' in html
    assert '<em>Ana</em>, bienvenida a For Rent Online.' in main
    assert 'cool-hero-full' in main
    assert 'Tu resumen' not in main
    assert 'Mis Clientes' not in main
    assert 'class="cool-kpis"' not in main
    assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más']
    # Inicio está resaltado.
    assert re.search(r'href="/portal"[^>]*class="on"', _bottomnav(html))


def test_kpi_coinciden_con_fases():
    html = cliente('ana').get('/mis-clientes').get_data(as_text=True)
    kpis = [int(x) for x in re.findall(r'class="cool-kpi-n">(\d+)</span>', html)]
    nums = [int(x) for x in re.findall(r'class="embudo-n">(\d+)</span>', html)]
    assert len(kpis) == 4
    assert len(nums) >= 4
    # KPI 1 = baldosa nuevas; KPI 2 = sin_estatus; KPI 3 = suma En Proceso (3 baldosas);
    # KPI 4 = suma En Negociación (3 baldosas). Orden de embudo-n: nuevas, sin, 3 proceso, 3 neg, ...
    assert kpis[0] == nums[0]
    assert kpis[1] == nums[1]
    assert kpis[2] == sum(nums[2:5])
    assert kpis[3] == sum(nums[5:8])


def test_modulos_tiene_estilo_cool():
    html = cliente('ana').get('/modulos').get_data(as_text=True)
    assert 'theme-cool' in html
    assert 'caracas-hero.webp' in html
    assert 'logo-blanco.webp' in html
    assert '>Módulos</h1>' in html
    assert 'Gestiona tu negocio inmobiliario' not in html
    assert 'class="cool-hi"' not in html
    assert 'class="cool-role"' not in html
    assert 'mod-inventario.webp' in html and 'mod-rendimiento.webp' in html
    assert 'mod-cool-sum' in html
    css = cliente('ana').get('/static/app.css').get_data(as_text=True)
    # Más aire bajo el título; las tarjetas no se estiran para llenar la pantalla.
    assert 'body.page-modulos > main{' in css and 'padding:24px 16px 10px' in css
    assert 'body.page-modulos .sec.mod{margin:0;flex:none' in css
    assert 'body.page-modulos .mod-cool-sum{min-height:64px;flex:none' in css
    assert _botones(_bottomnav(html)) == ['Inicio', 'Módulos', 'Mensajes', 'Más']
    assert re.search(r'href="/modulos"[^>]*class="on"', _bottomnav(html))
    # Siguen cerrados y con las mismas subopciones.
    assert len(re.findall(r'<details class="sec mod" id="mod-[^"]+"\s*>', html)) == 5
    assert 'En preparación' in html


def test_login_tiene_estilo_cool():
    from test_bandeja import appmod
    html = appmod.app.test_client().get('/login').get_data(as_text=True)
    assert 'caracas-hero.webp' in html
    assert 'logo-blanco.webp' in html
    assert '¡Bienvenido a For Rent Online!' in html
    assert 'Gestiona tus inmuebles, clientes, operaciones y comisiones en un solo lugar.' in html
    assert 'name="usuario"' in html and 'name="password"' in html
    assert 'Recordar mi usuario' in html
    assert 'data-ojo' in html
    assert 'forrent.usuario' in html
    assert 'localStorage' in html
    # No se inventa una recuperación que la app todavía no tiene.
    assert 'Olvidaste' not in html and 'olvidaste' not in html
    assert 'method="post"' in html and 'action="/login"' in html
    assert 'Periodismodepaz' in html
