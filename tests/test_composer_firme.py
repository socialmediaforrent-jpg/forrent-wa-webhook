"""Regresión: Responder nunca queda detrás de la barra, en todas las rutas de apertura."""
import os
import threading
from urllib.parse import quote

import pytest

from test_bandeja import CLI_ANA, cliente, entrante
import app as appmod

playwright = pytest.importorskip('playwright')
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

CHROME = '/usr/bin/google-chrome'
ROUTES = ('url', 'lista', 'atras', 'modulos')
DEVICES = ('473x1024', 'pixel7')

MEASURE_JS = """() => {
  const btn = document.getElementById('reply-btn');
  const nota = document.getElementById('reply-nota');
  const reply = document.getElementById('reply');
  const nav = document.querySelector('.bottomnav');
  const shell = document.querySelector('.inbox-shell');
  const vv = window.visualViewport;
  const navSt = nav ? getComputedStyle(nav) : null;
  const navVisible = !!(nav && navSt && navSt.display !== 'none' && navSt.visibility !== 'hidden');
  const nr = navVisible ? nav.getBoundingClientRect() : null;
  const br = btn ? btn.getBoundingClientRect() : null;
  const rr = reply ? reply.getBoundingClientRect() : null;
  const nrNota = nota ? nota.getBoundingClientRect() : null;
  const layoutH = window.innerHeight;
  const visBottom = vv ? (vv.offsetTop + vv.height) : layoutH;
  const limit = navVisible ? nr.top : visBottom;
  return {
    innerHeight: layoutH,
    visBottom,
    navVisible,
    navPos: nav ? navSt.position : null,
    navTop: nr ? nr.top : null,
    navHeight: nr ? nr.height : null,
    btnBottom: br ? br.bottom : null,
    notaBottom: nrNota ? nrNota.bottom : null,
    replyBottom: rr ? rr.bottom : null,
    okBtn: !!(br && br.bottom <= limit + 0.5 && br.bottom <= visBottom + 0.5 && br.top >= 0),
    okNota: !!(nrNota && nrNota.bottom <= limit + 0.5 && nrNota.bottom <= visBottom + 0.5),
    okReply: !!(rr && rr.bottom <= limit + 0.5 && rr.bottom <= visBottom + 0.5),
    shellPos: shell ? getComputedStyle(shell).position : null,
    href: location.pathname + location.search,
  };
}"""

MISMATCH_JS = """() => {
  const extra = 96;
  const h = (window.innerHeight + extra) + 'px';
  document.documentElement.style.height = h;
  document.documentElement.style.maxHeight = h;
  document.body.style.height = h;
  document.body.style.maxHeight = h;
}"""


def _device_opts(pw, name):
    if name == 'pixel7':
        return {k: v for k, v in pw.devices['Pixel 7'].items() if k in (
            'viewport', 'device_scale_factor', 'is_mobile', 'has_touch',
            'user_agent', 'screen', 'default_browser_type')}
    return {
        'viewport': {'width': 473, 'height': 1024},
        'device_scale_factor': 2,
        'is_mobile': True,
        'has_touch': True,
        'user_agent': ('Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36'),
        'screen': {'width': 473, 'height': 1120},
    }


@pytest.fixture
def hilo():
    appmod.q('DELETE FROM wa_events')
    appmod.q('DELETE FROM mensajes_last_seen')
    for i in range(8):
        entrante(CLI_ANA, horas_atras=1 + i * 0.2,
                 texto=f'Hola, busco un apartamento ({i+1})')
    yield CLI_ANA
    appmod.q('DELETE FROM wa_events')
    appmod.q('DELETE FROM mensajes_last_seen')


@pytest.fixture(scope='module')
def servidor():
    os.environ['GHL_SYNC_DISABLED'] = '1'
    server = make_server('127.0.0.1', 0, appmod.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


@pytest.fixture(scope='module')
def browser():
    if not os.path.exists(CHROME):
        pytest.skip('no está Google Chrome')
    with sync_playwright() as pw:
        b = pw.chromium.launch(
            executable_path=CHROME,
            args=['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
        )
        yield pw, b
        b.close()


def _cookie():
    c = cliente('jose')
    ck = c.get_cookie('forrent_session', domain='localhost') or c.get_cookie('session')
    assert ck is not None
    return ck.value


def _open(page, base, route, wa_id):
    sel = f'a.convo[href*="{quote(wa_id, safe="")}"]'
    if route == 'url':
        page.goto(f'{base}/panel?c={quote(wa_id, safe="")}', wait_until='networkidle')
    elif route == 'lista':
        page.goto(f'{base}/panel', wait_until='networkidle')
        page.wait_for_selector(sel)
        page.click(sel)
        page.wait_for_selector('#reply-btn')
    elif route == 'atras':
        page.goto(f'{base}/panel?c={quote(wa_id, safe="")}', wait_until='networkidle')
        page.wait_for_selector('.thread-head .back')
        page.click('.thread-head .back')
        page.wait_for_selector(sel)
        page.click(sel)
        page.wait_for_selector('#reply-btn')
    elif route == 'modulos':
        page.goto(f'{base}/panel?c={quote(wa_id, safe="")}', wait_until='networkidle')
        page.wait_for_selector('.bottomnav a[href="/modulos"]')
        page.click('.bottomnav a[href="/modulos"]')
        page.wait_for_url('**/modulos')
        page.wait_for_selector('.bottomnav a[href="/panel"]')
        page.click('.bottomnav a[href="/panel"]')
        page.wait_for_selector(sel)
        page.click(sel)
        page.wait_for_selector('#reply-btn')
    else:
        raise ValueError(route)
    page.wait_for_timeout(250)


def _context(pw, browser, device):
    ctx = browser.new_context(**_device_opts(pw, device))
    ctx.add_cookies([{'name': 'forrent_session', 'value': _cookie(),
                      'domain': '127.0.0.1', 'path': '/'}])
    page = ctx.new_page()
    page.add_init_script('try{sessionStorage.clear()}catch(e){}')
    return ctx, page


@pytest.mark.parametrize('device', DEVICES)
@pytest.mark.parametrize('route', ROUTES)
def test_responder_queda_encima_de_la_barra(servidor, browser, hilo, device, route):
    pw, b = browser
    ctx, page = _context(pw, b, device)
    try:
        _open(page, servidor, route, hilo)
        m = page.evaluate(MEASURE_JS)
        assert m['navVisible'], m
        assert m['okBtn'], f'Responder queda detrás o fuera de la barra: {m}'
        assert m['okNota'], f'El aviso de 24 h queda detrás de la barra: {m}'
        assert m['okReply'], f'La caja de escribir queda detrás de la barra: {m}'
        assert m['navPos'] == 'static', m
        assert m['shellPos'] == 'fixed', m
    finally:
        ctx.close()


@pytest.mark.parametrize('device', DEVICES)
def test_desajuste_de_100dvh_no_tapa_el_compositor(servidor, browser, hilo, device):
    """Si 100dvh queda más alto que el viewport (barra de Chrome), el compositor sigue arriba."""
    pw, b = browser
    ctx, page = _context(pw, b, device)
    try:
        _open(page, servidor, 'url', hilo)
        page.evaluate(MISMATCH_JS)
        page.wait_for_timeout(80)
        m = page.evaluate(MEASURE_JS)
        assert m['navVisible'], m
        assert m['okBtn'], f'Con 100dvh inflado, Responder queda tapado: {m}'
        assert m['okNota'], f'Con 100dvh inflado, el aviso de 24 h queda tapado: {m}'
        assert m['okReply'], f'Con 100dvh inflado, la caja queda tapada: {m}'
    finally:
        ctx.close()
