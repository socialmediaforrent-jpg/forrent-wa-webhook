"""Inmuebles estilo Wasi: lista, filtros y ficha. Solo lectura; permisos iguales; sin enlaces ajenos. SQLite local, sin red."""
import hashlib, json, os, re, sys, tempfile
from urllib.parse import quote

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if 'app' not in sys.modules:
    _tmp = tempfile.mkdtemp()
    os.environ['SQLITE_PATH'] = os.path.join(_tmp, 't.db')
    os.environ.pop('DATABASE_URL', None)
    os.environ['PANEL_PASSWORD'] = 'clave-jose'
    os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest()})
    for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX', 'META_PAGE_TOKEN'):
        os.environ.pop(k, None)
    os.chdir(_tmp)

import app as appmod  # noqa: E402
import inmuebles, portal_lib  # noqa: E402

FOTO = {'full': 'https://images.wasi.co/inmuebles/1.jpg', 'thumb': 'https://images.wasi.co/inmuebles/1.jpg'}
INV = {'actualizado': '2026-11-16T07:00:00-04:00', 'grupos': {
    'propio': [
        {'titulo': 'Local Comercial En Alquiler Av. Urdaneta 7 m2', 'operacion': 'Alquiler', 'tipo': 'Local', 'zona': 'Av. Urdaneta',
         'precio': 'US$ 300', 'codigo': '10542820', 'origen': 'propio', 'solo_cliente_directo': False, 'fotos': [FOTO, FOTO | {'full': 'https://images.wasi.co/inmuebles/2.jpg'}],
         'm2': '7', 'banos': '1', 'ciudad': 'Caracas', 'region': 'Miranda - Dtto. Capital', 'pais': 'Venezuela',
         'estado_pub': 'Active', 'estado_fisico': 'Used', 'descripcion': '<p>Local <b>céntrico</b></p><script>alert(1)</script>',
         'actualizado_en': '2026-10-31T13:30:25'},
        {'titulo': 'Apartamento en venta El Cafetal', 'operacion': 'Venta', 'tipo': 'Apartamento', 'zona': 'El Cafetal',
         'precio': 'US$ 120.000', 'codigo': '10500001', 'origen': 'propio', 'solo_cliente_directo': False,
         'habitaciones': '3', 'm2': '110', 'estado_pub': 'Outstanding', 'actualizado_en': '2026-11-10T10:00:00'},
    ],
    'alianza': [
        {'titulo': 'Cubículo en El Rosal', 'operacion': 'Alquiler', 'tipo': 'Cubículo', 'zona': 'El Rosal', 'precio': 'US$ 400',
         'codigo': '2327223429', 'origen': 'alianza', 'solo_cliente_directo': True, 'fotos': []},
    ],
    'red': [
        {'titulo': 'RED NO DEBE SALIR', 'operacion': 'Venta', 'tipo': 'Casa', 'zona': 'Prados', 'precio': 'US$ 1', 'codigo': '999', 'origen': 'red'},
    ]}}
CAPS = [{'codigo': '10542820', 'origen': 'propio', 'asesor': 'ana', 'titulo': 'x'}]


@pytest.fixture(autouse=True)
def datos(monkeypatch):
    monkeypatch.setattr(appmod, '_inventario', lambda: json.loads(json.dumps(INV)))
    monkeypatch.setattr(portal_lib, 'load_captaciones', lambda *a, **k: list(CAPS))
    yield


def cliente(slug):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s['uid'] = slug
    return c


def html(r):
    return r.get_data(as_text=True)


def test_requiere_login():
    r = appmod.app.test_client().get('/inventario')
    assert r.status_code in (302, 401)


def test_lista_estilo_wasi_y_sin_red():
    h = html(cliente('ana').get('/inventario'))
    assert 'Inmuebles' in h and 'resultados' in h and 'Filtrar' in h
    assert 'Local Comercial En Alquiler' in h and 'Cubículo en El Rosal' in h
    assert 'RED NO DEBE SALIR' not in h
    assert 'Activo para difusión' in h and 'Destacado' in h and 'Local para Alquiler' in h
    assert 'En Av. Urdaneta, Caracas, Miranda - Dtto. Capital, Venezuela' in h
    assert '7.0 m²' in h and '1/2' in h


def test_encargado_solo_presidencia_y_mercadeo():
    assert 'Encargado del inmueble' not in html(cliente('ana').get('/inventario'))
    h = html(cliente('jose').get('/inventario'))
    assert 'Encargado del inmueble' in h


def test_sin_enlaces_de_portales_ni_ajenos():
    """La lista de inmuebles no lleva URLs de Rex, Mercado Libre ni Wasi. En la ficha sí pueden
    aparecer, pero solo dentro de la barra Portales Inmobiliarios (enlaces verificados)."""
    for u in ('ana', 'jose'):
        h = html(cliente(u).get('/inventario'))
        assert 'rexup.net' not in h and 'mercadolibre.com.ve' not in h and 'grupoinmobiliarioforrent' not in h


@pytest.mark.parametrize('qs,esperados,no', [
    ('op=Venta', ['El Cafetal'], ['Av. Urdaneta 7', 'El Rosal']),
    ('tipo=Local&tipo=Cubículo', ['Av. Urdaneta 7', 'El Rosal'], ['El Cafetal']),
    ('pmin=350&pmax=1000', ['El Rosal'], ['Av. Urdaneta 7', 'El Cafetal']),
    ('lista=aliados', ['El Rosal'], ['Av. Urdaneta 7']),
    ('f=propias', ['Av. Urdaneta 7', 'El Cafetal'], ['El Rosal']),
    ('hab=2', ['El Cafetal'], ['El Rosal']),
    ('amin=50', ['El Cafetal'], ['Av. Urdaneta 7']),
    ('fotos=1', ['Av. Urdaneta 7'], ['El Rosal']),
    ('q=10542820', ['Av. Urdaneta 7'], ['El Cafetal']),
    ('estado=Destacado', ['El Cafetal'], ['Av. Urdaneta 7']),
    ('lista=mias', ['Av. Urdaneta 7'], ['El Cafetal', 'El Rosal']),
])
def test_filtros(qs, esperados, no):
    h = html(cliente('ana').get('/inventario?' + qs))
    lista = h.split('id="wz-lista"')[1].split('id="wz-mapa"')[0]
    for e in esperados:
        assert e in lista, (qs, e)
    for e in no:
        assert e not in lista, (qs, e)


def test_orden_precio():
    h = html(cliente('ana').get('/inventario?orden=mayor'))
    assert h.index('El Cafetal') < h.index('El Rosal') < h.index('Av. Urdaneta 7')


def test_ficha_detalle():
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820')))
    assert 'Activo para difusión' in h and 'Generar PDF' in h and 'Usado' in h and 'Sin datos' in h
    assert 'céntrico' in h and '<script>alert(1)' not in h
    assert 'Encargado del inmueble' not in h
    assert 'Encargado del inmueble' in html(cliente('jose').get('/inventario/ver?k=' + quote('c:10542820')))


def test_ficha_detalles_plegable():
    """La cuadrícula de datos va en una barra «Detalles» cerrada; título, ubicación y precio siguen visibles."""
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820')))
    m = re.search(r'<details class="wz-detalles"(?P<attrs>[^>]*)>(?P<body>.*?)</details>', h, re.S)
    assert m, 'falta la barra Detalles'
    assert 'open' not in (m.group('attrs') or '')
    assert '>Detalles' in m.group(0) and 'class="ch"' in m.group(0)
    for lbl in ('Estado físico', 'Año construcción', 'Área privada', 'Área construida',
                'Área terreno', 'Alcobas', 'Baños', 'Pisos', 'Estacionamiento', 'Código'):
        assert lbl in m.group('body'), lbl
    fuera = h[:m.start()] + h[m.end():]
    assert 'wz-tn' in fuera and 'wz-loc' in fuera and 'wz-pband' in fuera
    # El título de esta ficha y el precio quedan fuera de la barra.
    assert 'Local Comercial En Alquiler' in fuera
    assert 'US$ 300' in fuera
    assert 'Estado físico' not in fuera


def test_ficha_portales_plegable():
    """La barra Portales Inmobiliarios va cerrada, con las 10 casillas, el contador y Marketplace."""
    key = 'c:10542820'
    # Enlace verificado de prueba (solo en esta ficha).
    it = next((x for xs in (INV['grupos']['propio'], INV['grupos']['alianza']) for x in xs
               if appmod.portales.key_de(x) == key), None)
    assert it, 'falta el inmueble de prueba'
    appmod.portales.guardar(
        appmod.q, it=it, portal='mercadolibre',
        url='https://www.mercadolibre.com.ve/inmueble-de-prueba-10542820',
        estado='ok', http_status=200, fuente='test', por='jose')
    h = html(cliente('ana').get('/inventario/ver?k=' + quote(key)))
    m = re.search(r'<details class="[^"]*wz-portales[^"]*"(?P<attrs>[^>]*)>(?P<body>.*?)</details>', h, re.S)
    assert m, 'falta la barra Portales Inmobiliarios'
    assert 'open' not in (m.group('attrs') or '')
    assert 'Portales Inmobiliarios' in m.group(0)
    assert '1/10' in m.group(0)
    assert 'Facebook Marketplace' in m.group('body')
    assert 'mercadolibre.com.ve' in m.group('body')
    assert 'class="pl vacia"' in m.group('body')
    # Ana ve el logo y puede abrir/copiar/compartir; no edita enlaces.
    assert 'Editar enlaces' not in h
    assert 'Agregar enlace de Marketplace' not in h
    # Título y precio siguen fuera.
    fuera = h[:m.start()] + h[m.end():]
    assert 'Local Comercial En Alquiler' in fuera
    assert 'US$ 300' in fuera
    # Presidencia sí edita desde la ficha.
    hj = html(cliente('jose').get('/inventario/ver?k=' + quote(key)))
    assert 'Editar enlaces' in hj
    assert 'Agregar enlace de Marketplace' in hj or 'Editar enlace de Marketplace' in hj
    # La lista sigue sin llevar esas URLs.
    lista = html(cliente('ana').get('/inventario'))
    assert 'mercadolibre.com.ve' not in lista


def test_ficha_redes_plegable():
    """La barra Redes Sociales va cerrada, con 6 redes (Pinterest incluida), y solo se llena con una publicación real."""
    import redes_inmueble
    key = 'c:10542820'
    it = next((x for xs in (INV['grupos']['propio'], INV['grupos']['alianza']) for x in xs
               if appmod.portales.key_de(x) == key), None)
    assert it
    url, err = redes_inmueble.url_valida('instagram', 'https://www.instagram.com/reel/DdofgI6lTZM/')
    assert err is None and url.endswith('/reel/DdofgI6lTZM/')
    redes_inmueble.guardar(appmod.q, key=key, codigo='10542820', red='instagram', url=url, por='jose', fuente='test')
    h = html(cliente('ana').get('/inventario/ver?k=' + quote(key)))
    m = re.search(r'<details class="[^"]*wz-redes[^"]*"(?P<attrs>[^>]*)>(?P<body>.*?)</details>', h, re.S)
    assert m, 'falta la barra Redes Sociales'
    assert 'open' not in (m.group('attrs') or '')
    assert 'Redes Sociales' in m.group(0)
    assert '1/6' in m.group(0)
    assert 'instagram.com/reel/DdofgI6lTZM' in m.group('body')
    assert m.group('body').count('class="pl vacia"') == 5  # FB, TikTok, YouTube, Pinterest, LinkedIn vacías
    # Ana no edita; Presidencia y Mercadeo sí.
    assert 'Editar enlaces de redes' not in h
    hj = html(cliente('jose').get('/inventario/ver?k=' + quote(key)))
    assert 'Editar enlaces de redes' in hj
    # La lista no lleva esas URLs.
    assert 'instagram.com/reel' not in html(cliente('ana').get('/inventario'))
    # No se acepta el perfil de For Rent como publicación del inmueble.
    assert redes_inmueble.url_valida('instagram', 'https://www.instagram.com/forrent.grupoinmobiliario')[0] is None
    assert redes_inmueble.url_valida('youtube', 'https://www.youtube.com/channel/UChfbVol9e7YtGsw_SqYR-qw')[0] is None
    assert [r['key'] for r in redes_inmueble.REDES] == [
        'instagram', 'facebook', 'tiktok', 'youtube', 'pinterest', 'linkedin']
    pin, err = redes_inmueble.url_valida('pinterest', 'https://www.pinterest.com/pin/1099370959080844583/')
    assert err is None and '/pin/1099370959080844583' in pin
    assert redes_inmueble.url_valida('pinterest', 'https://www.pinterest.com/forrentgrupo/')[0] is None


def test_redes_inmueble_permisos_y_ruta():
    """Una asesora no edita; Presidencia y Mercadeo sí, y vuelven a la ficha."""
    import redes_inmueble
    def csrf(slug):
        return portal_lib.csrf_token(portal_lib.user_by_username(slug))
    key = 'c:10542820'
    data = {'key': key, 'red': 'tiktok', 'url': 'https://www.tiktok.com/@forrent.grupoinmo/video/7694300463755250951',
            'csrf': csrf('ana'), 'vuelta': '/inventario/ver?k=' + key}
    assert cliente('ana').post('/mercadeo/redes', data=data).status_code == 403
    r = cliente('jose').post('/mercadeo/redes', data={**data, 'csrf': csrf('jose')})
    assert r.status_code in (302, 303)
    loc = r.headers['Location']
    assert 'ok=red' in loc and '/inventario/ver' in loc
    links = redes_inmueble.links_por_key(appmod.q).get(key) or {}
    assert 'tiktok' in links and 'video/7694300463755250951' in links['tiktok']['url']
    # URL inválida (perfil) no se guarda.
    r = cliente('jose').post('/mercadeo/redes', data={**data, 'red': 'facebook',
        'url': 'https://www.facebook.com/552585004598339', 'csrf': csrf('jose')})
    assert r.status_code in (302, 303) and 'redes_err=' in r.headers['Location']


# JPEG mínimo (1×1) para no depender de archivos grandes en el test.
_JPEG_1 = (b'\xff\xd8\xff\xdb\x00C\x00' + b'\x03' * 64 +
           b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
           b'\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x03'
           b'\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
           b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x37\xff\xd9')


def test_fotos_locales_si_el_inventario_no_trae_url(tmp_path, monkeypatch):
    """Inmuebles Rex (u otra fuente) sin URL de Wasi/ML usan la galería local por código."""
    d = tmp_path / '20545795'
    d.mkdir()
    (d / '01.jpg').write_bytes(_JPEG_1)
    monkeypatch.setattr(appmod, '_INV_FOTOS_DIR', str(tmp_path))
    it = {'titulo': 'Apartamento en La Bonita (Guaicay)', 'operacion': 'Venta', 'tipo': 'Apartamento',
          'zona': 'La Bonita (Guaicay)', 'precio': 'US$ 126.000', 'codigo': '20545795',
          'origen': 'propio', 'fotos': []}
    f = appmod._ficha_publica(it)
    assert f['fotos'] and '/static/inv-fotos/20545795/01.jpg' in f['fotos'][0]['full']
    # Si ya hay foto de Wasi, no se sustituye.
    it['fotos'] = [FOTO]
    f2 = appmod._ficha_publica(it)
    assert f2['fotos'][0]['full'] == FOTO['full']
    # No se inventa una ruta fuera del código.
    assert appmod._fotos_estaticas('../etc') == []
    assert appmod._fotos_estaticas('') == []


def test_ficha_inexistente_y_red_404():
    assert cliente('ana').get('/inventario/ver?k=nada').status_code == 404
    assert cliente('ana').get('/inventario/ver?k=' + quote('c:999')).status_code == 404


def test_volver_solo_interno():
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820') + '&volver=' + quote('https://malo.com')))
    assert 'malo.com' not in h
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820') + '&volver=' + quote('/inventario?op=Venta')))
    assert 'href="/inventario?op=Venta"' in h


def test_compartir_sin_enlaces():
    f = inmuebles.ficha(INV['grupos']['propio'][0], appmod._ficha_publica(INV['grupos']['propio'][0]))
    t = inmuebles.compartir_texto(f)
    assert 'http' not in t and '10542820' in t and 'US$ 300' in t


def test_texto_plano():
    assert inmuebles.texto_plano('<p>Hola&nbsp;<b>mundo</b></p><script>x</script>') == 'Hola\xa0mundo'


# ------------------------------------------------------------ Rol Inventario (solo lectura de Inmuebles)
@pytest.fixture
def inv_pw(monkeypatch):
    monkeypatch.setenv('INVENTARIO_PASSWORD_SHA256', hashlib.sha256(b'clave-inv').hexdigest())
    monkeypatch.delenv('INVENTARIO_PASSWORD', raising=False)
    yield


def test_login_inventario(inv_pw):
    assert portal_lib.identify_user('inventario', 'clave-inv')['role'] == 'inventario'
    assert portal_lib.identify_user('Inventario', 'mala') is None


def test_inventario_sin_variable_no_entra(monkeypatch):
    monkeypatch.delenv('INVENTARIO_PASSWORD_SHA256', raising=False)
    monkeypatch.delenv('INVENTARIO_PASSWORD', raising=False)
    assert portal_lib.identify_user('inventario', 'cualquiera') is None
    assert portal_lib.user_by_username('inventario') is None
    assert cliente('inventario').get('/inventario').status_code in (302, 401)


def test_inventario_ve_inmuebles_con_encargado_y_menu(inv_pw):
    c = cliente('inventario')
    h = html(c.get('/inventario'))
    assert 'Encargado del inmueble' in h and 'Ana' in h and 'RED NO DEBE SALIR' not in h
    assert '>Inmuebles<' in h and 'Inventario General' not in h
    assert 'href="/mis-captaciones"' not in h and 'value="mias"' not in h
    d = html(c.get('/inventario/ver?k=' + quote('c:10542820')))
    assert 'Encargado del inmueble' in d and 'href="/mercadeo"' not in d
    r = c.get('/portal')
    assert r.status_code == 200 and 'bienvenido a For Rent Online' in r.get_data(as_text=True)


def test_inventario_no_entra_a_nada_mas(inv_pw):
    """Toda ruta con sesión distinta de Inmuebles responde 403 a Inventario (GET y POST)."""
    anon, c = appmod.app.test_client(), cliente('inventario')
    revisadas = 0
    for rule in appmod.app.url_map.iter_rules():
        if rule.arguments or rule.endpoint in ('static', 'inventario', 'inventario_ver', 'carga_cola', 'carga_agentiz', 'portal', 'login', 'logout'):
            continue
        for m in ('GET', 'POST'):
            if m not in rule.methods:
                continue
            ra = anon.open(rule.rule, method=m)
            protegida = ra.status_code in (302, 401) and '/login' in (ra.headers.get('Location') or '/login')
            if not protegida:
                continue
            rc = c.open(rule.rule, method=m)
            assert rc.status_code in (401, 403), (rule.rule, m, rc.status_code)
            revisadas += 1
    assert revisadas > 20


def test_otros_roles_sin_cambios():
    assert 'Encargado del inmueble' not in html(cliente('ana').get('/inventario'))
    assert cliente('ana').get('/mis-clientes').status_code == 200
    h = html(cliente('jose').get('/inventario'))
    assert 'Encargado del inmueble' in h and '>Inmuebles<' in h


def test_lista_compacta_numerada_empieza_cerrada():
    h = html(cliente('ana').get('/inventario'))
    lista = h.split('id="wz-lista"')[1].split('id="wz-mapa"')[0]
    assert 'class="wz-list"' in lista
    # Tocar la fila abre la ficha directamente (sin panel intermedio).
    assert 'data-inm-toggle' not in lista
    assert re.search(r'<a class="wz-sum" href="/inventario/ver\?k=', lista)
    assert 'data-compartir' in lista
    cards = re.findall(r'<div class="wz-card"[^>]*>', lista)
    assert len(cards) == 3 and all('hidden' in c for c in cards)
    nums = re.findall(r'class="wz-n"[^>]*>(\d+)</span>', lista)
    assert nums == ['1', '2', '3'], nums
    # La fila compacta trae lo esencial; la tarjeta completa sigue en el HTML, oculta.
    assert '10542820' in lista and 'Alquiler' in lista and 'US$ 300' in lista


def test_numeracion_sigue_filtro_y_orden():
    h = html(cliente('ana').get('/inventario?lista=propias'))
    lista = h.split('id="wz-lista"')[1].split('id="wz-mapa"')[0]
    assert re.findall(r'class="wz-n"[^>]*>(\d+)</span>', lista) == ['1', '2']
    assert 'Cubículo en El Rosal' not in lista
    h = html(cliente('ana').get('/inventario?orden=mayor'))
    lista = h.split('id="wz-lista"')[1].split('id="wz-mapa"')[0]
    assert re.findall(r'class="wz-n"[^>]*>(\d+)</span>', lista) == ['1', '2', '3']
    assert lista.index('El Cafetal') < lista.index('El Rosal') < lista.index('Av. Urdaneta 7')
    # El 1 es el primero del orden aplicado (mayor precio).
    first = re.search(r'class="wz-item".*?</li>', lista, re.S).group(0)
    assert 'class="wz-n"' in first and '>1</span>' in first and 'El Cafetal' in first


def test_mis_captaciones_lista_numerada_cerrada():
    h = html(cliente('ana').get('/mis-captaciones'))
    assert 'class="inv-n"' in h
    assert re.findall(r'class="inv-n"[^>]*>(\d+)</span>', h) == ['1']
    exp = re.findall(r'<div class="inv-ficha-exp"[^>]*>', h)
    assert exp and all('hidden' in e for e in exp)
    assert 'inv-modal' not in h
    assert re.search(r'<a class="inv-row" href="/inventario/ver\?k=[^"]*volver=/mis-captaciones', h)


def test_mis_captaciones_retiro_en_la_ficha_no_en_bloque_grande():
    h = html(cliente('ana').get('/mis-captaciones'))
    assert 'Solicitar retiro de' not in h
    assert 'select name="codigo"' not in h
    assert 'Agregar captación' in h
    assert '+ Solicitar agregar' not in h
    assert 'Cargar inmueble' not in h
    assert 'href="/inventario/ver?k=' in h and 'volver=/mis-captaciones' in h
    # El retiro pasa a la ficha del inmueble (solo para la captadora, no para Presidencia).
    k = quote('c:10542820')
    f = html(cliente('ana').get('/inventario/ver?k=' + k))
    assert 'Retirar captación' in f and 'Solicitar retiro' in f
    assert 'name="accion" value="baja"' in f and 'value="10542820"' in f and 'name="nota"' in f
    assert re.search(r'name="csrf" value="[^"]+"', f.split('wz-retirar')[1])
    assert 'Copiar código' in f
    fj = html(cliente('jose').get('/inventario/ver?k=' + k))
    assert 'Retirar captación' not in fj
    hj = html(cliente('jose').get('/mis-captaciones'))
    assert 'inv-accion-retiro' not in hj and 'Retirar captación' not in hj
    assert '+ Solicitar agregar' not in hj and 'Cargar inmueble' not in hj
    assert 'Agregar captación' in hj


def test_solicitar_retiro_sigue_el_mismo_flujo():
    c = cliente('ana')
    token = portal_lib.csrf_token(portal_lib.user_by_username('ana'))
    s0 = appmod.q('SELECT COUNT(*) AS n FROM captacion_solicitudes', fetch=True)[0]['n']
    r = c.post('/mis-captaciones', data={
        'csrf': token, 'accion': 'baja', 'codigo': '10542820',
        'titulo': 'Local Comercial En Alquiler Av. Urdaneta 7 m2',
        'origen': 'propio', 'nota': 'Ya no lo trabajo', 'idem': 'tok-baja-ficha',
    })
    assert r.status_code == 302 and r.headers['Location'].endswith('/mis-captaciones?ok=1')
    rows = appmod.q('SELECT * FROM captacion_solicitudes ORDER BY id DESC', fetch=True)
    assert rows and rows[0]['accion'] == 'baja' and rows[0]['codigo'] == '10542820'
    assert rows[0]['nota'] == 'Ya no lo trabajo' and rows[0]['estado'] == 'pendiente'
    assert appmod.q('SELECT COUNT(*) AS n FROM captacion_solicitudes', fetch=True)[0]['n'] == s0 + 1


def test_volver_desde_ficha_acepta_mis_captaciones():
    assert appmod._volver_inv('/mis-captaciones') == '/mis-captaciones'
    assert appmod._volver_inv('/mis-captaciones?f=todas') == '/mis-captaciones?f=todas'
    assert appmod._volver_inv('/inventario?lista=propias') == '/inventario?lista=propias'
    assert appmod._volver_inv('https://evil.example/x') == '/inventario'
    assert appmod._volver_inv('/otra') == '/inventario'


def test_agregar_captacion_solicitud_de_existente_sigue_el_mismo_flujo():
    c = cliente('ana')
    token = portal_lib.csrf_token(portal_lib.user_by_username('ana'))
    s0 = appmod.q('SELECT COUNT(*) AS n FROM captacion_solicitudes', fetch=True)[0]['n']
    r = c.post('/mis-captaciones', data={
        'csrf': token, 'accion': 'alta', 'titulo': 'Local ya en inventario',
        'codigo': '10542820', 'origen': 'propio', 'nota': 'Es mío', 'idem': 'tok-alta-unificado',
    })
    assert r.status_code == 302 and r.headers['Location'].endswith('/mis-captaciones?ok=1')
    rows = appmod.q('SELECT * FROM captacion_solicitudes ORDER BY id DESC', fetch=True)
    assert rows and rows[0]['accion'] == 'alta' and rows[0]['codigo'] == '10542820'
    assert rows[0]['titulo'] == 'Local ya en inventario' and rows[0]['estado'] == 'pendiente'
    assert appmod.q('SELECT COUNT(*) AS n FROM captacion_solicitudes', fetch=True)[0]['n'] == s0 + 1


def test_agregar_captacion_error_vuelve_al_formulario():
    c = cliente('ana')
    token = portal_lib.csrf_token(portal_lib.user_by_username('ana'))
    r = c.post('/mis-captaciones', data={'csrf': token, 'accion': 'alta', 'titulo': 'ab', 'idem': 'tok-alta-corto'})
    assert r.status_code == 400
    h = r.get_data(as_text=True)
    assert 'Indica el título o la dirección del inmueble.' in h
    assert 'name="accion" value="alta"' in h
    assert 'value="ab"' in h


def test_inventario_de_la_app_omite_la_red():
    raw = json.loads(json.dumps(INV))
    raw['grupos']['propio'].append({
        'titulo': 'RED COLADA', 'operacion': 'Venta', 'tipo': 'Casa', 'zona': 'Prados',
        'precio': 'US$ 2', 'codigo': '888', 'origen': 'red',
    })
    out = appmod._inventario_sin_red(raw)
    assert set(out['grupos']) == {'propio', 'alianza'}
    assert out['conteos'] == {'propio': 2, 'alianza': 1}
    blob = json.dumps(out, ensure_ascii=False)
    assert 'RED NO DEBE SALIR' not in blob and 'RED COLADA' not in blob
    assert 'Cubículo en El Rosal' in blob and 'Local Comercial En Alquiler' in blob


def test_inventario_pantalla_usa_conteos_sin_red(monkeypatch):
    monkeypatch.setattr(appmod, '_inventario_raw', lambda: json.loads(json.dumps(INV)))
    monkeypatch.setattr(appmod, '_inventario', lambda: appmod._inventario_sin_red(appmod._inventario_raw()))
    h = html(cliente('jose').get('/inventario'))
    assert 'RED NO DEBE SALIR' not in h
    assert 'Propias (2)' in h and 'aliados (1)' in h
    assert '>3</b> resultados' in h


def test_mis_captaciones_solo_titulo_y_pestanas():
    h = html(cliente('ana').get('/mis-captaciones'))
    assert 'Mis Captaciones' in h
    assert 'Agregar captación' in h
    assert 'Propios (' in h and 'Alianza (' in h and 'Todos (' in h
    assert 'Solo donde figuras como captadora' not in h
    assert 'Ver Inmuebles' not in h
    assert 'Para agregar, toca' not in h
    assert 'Inventario del' not in h


def test_ficha_sin_barra_inmueble():
    """La ficha ya no lleva la barra «Inmueble»; se vuelve con el «Atrás» del encabezado blanco."""
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820')))
    assert 'class="wz-bar' not in h and '<h1 class="c">Inmueble</h1>' not in h
    assert 'class="btn-atras"' in h and 'href="/inventario"' in h


def test_ficha_pdf_y_sin_correo():
    """Generar PDF abre la ficha A4 con marca For Rent; ya no hay «Enviar correo»."""
    key = 'c:10542820'
    h = html(cliente('ana').get('/inventario/ver?k=' + quote(key)))
    assert 'Enviar correo' not in h and 'mailto:' not in h
    assert '/inventario/ver/pdf?k=' in h and 'Compartir' in h
    p = html(cliente('ana').get('/inventario/ver/pdf?k=' + quote(key)))
    assert '/static/logo.png' in p and 'For Rent Grupo Inmobiliario, C.A.' in p
    assert '+58 424-2294196' in p
    assert 'window.print' not in p
    assert 'window.print' in html(cliente('ana').get('/inventario/ver/pdf?auto=1&k=' + quote(key)))


def test_ficha_pestanas_clientes_ocultas():
    """Detalles / Clientes enlazados / Clientes potenciales siguen en el código pero ocultas."""
    h = html(cliente('ana').get('/inventario/ver?k=' + quote('c:10542820')))
    assert '<div class="wz-tabs" role="tablist" hidden>' in h
