"""El compositor de Mensajes queda completo encima de la barra inferior."""
import os
import re

import pytest

from test_bandeja import CLI_ANA, ROOT, cliente, entrante  # noqa: F401
import app as appmod  # noqa: E402


def _limpiar_estado():
    appmod.q('DELETE FROM wa_events')
    appmod.q('DELETE FROM mensajes_last_seen')


@pytest.fixture(autouse=True)
def _limpiar_eventos():
    _limpiar_estado()
    yield
    _limpiar_estado()


def _panel_html():
    return open(os.path.join(ROOT, 'templates', 'panel.html'), encoding='utf-8').read()


def _css_panel():
    html = _panel_html()
    m = re.search(r'<style>(.*?)</style>', html, re.S)
    assert m, 'falta el CSS de Mensajes'
    return m.group(1)


def _regla(css, selector):
    m = re.search(re.escape(selector) + r'\{[^}]+\}', css)
    assert m, f'falta la regla {selector}'
    return m.group(0)


def test_css_body_inbox_no_usa_padding_del_body():
    """El padding inferior del body se come en el flex de 100dvh; aquí va a cero."""
    body = _regla(_css_panel(), 'body.inbox-page')
    assert 'padding-bottom:0' in body
    assert '100dvh' in body
    assert 'overflow:hidden' in body


def test_css_espacio_reserva_barra_y_safe_area():
    css = _css_panel()
    assert '@media (max-width:959px)' in css
    base = _regla(css, '.inbox-nav-space')
    assert 'flex:none' in base
    idx = css.find('@media (max-width:959px)')
    bloque = css[idx:idx + 800]
    assert 'var(--bottom)' in bloque
    assert 'safe-area-inset-bottom' in bloque
    assert '--inbox-nav' in bloque
    assert 'var(--kb, 0px)' in bloque or 'var(--kb,0px)' in bloque
    assert 'kb-open' in bloque
    assert '#reply-text:focus' in bloque
    assert 'bottomnav' in bloque and 'display:none' in bloque


def test_viewport_se_achica_con_el_teclado():
    html = _panel_html()
    assert 'interactive-widget=resizes-content' in html
    assert 'visualViewport' in html
    assert "setProperty('--inbox-nav'" in html or 'setProperty("--inbox-nav"' in html
    assert "setProperty('--kb'" in html or 'setProperty("--kb"' in html


def test_conversacion_lleva_compositor_aviso_y_espacio():
    entrante(CLI_ANA, texto='Hola, busco un apartamento')
    html = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    assert 'class="inbox-nav-space"' in html
    assert 'id="reply"' in html
    assert 'id="reply-nota"' in html
    assert 'Escribe una respuesta' in html or 'id="reply-text"' in html
    assert 'class="nota' in html
