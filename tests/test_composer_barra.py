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
    m = re.search(r'<style[^>]*>(.*?)</style>', html, re.S)
    assert m, 'falta el CSS de Mensajes'
    return m.group(1)


def _regla(css, selector):
    m = re.search(re.escape(selector) + r'\{[^}]+\}', css)
    assert m, f'falta la regla {selector}'
    return m.group(0)


def test_css_body_inbox_no_usa_padding_del_body():
    """El cascarón fixed inset:0 sustituye el padding del body en el flex de 100dvh."""
    body = _regla(_css_panel(), 'body.inbox-page')
    assert 'padding-bottom:0' in body
    assert '100dvh' in body
    assert 'overflow:hidden' in body


def test_css_cascaron_fijo_y_barra_en_el_flujo():
    """La conversación vive en un cascarón fixed inset:0; la barra no va superpuesta."""
    css = _css_panel()
    shell = _regla(css, '.inbox-shell')
    assert 'position:fixed' in shell
    assert 'inset:0' in shell
    assert 'display:flex' in shell
    assert 'flex-direction:column' in shell
    assert 'height:100%' in shell
    inbox = _regla(css, '.inbox')
    assert 'flex:1' in inbox
    assert 'order:1' in inbox
    assert 'min-height:0' in inbox
    assert '@media (max-width:959px)' in css
    idx = css.find('@media (max-width:959px)')
    bloque = css[idx:idx + 900]
    assert 'position:static' in bloque
    assert 'order:2' in bloque
    assert 'body.inbox-page .bottomnav' in bloque
    assert 'var(--kb, 0px)' in bloque or 'var(--kb,0px)' in bloque
    assert 'kb-open' in bloque
    assert '#reply-text:focus' in bloque
    assert 'bottomnav' in bloque and 'display:none' in bloque
    # El hueco de la barra ya no se mide con JS ni con --inbox-nav.
    assert '--inbox-nav' not in css
    assert '.inbox-nav-space' not in css


def test_viewport_se_achica_con_el_teclado():
    html = _panel_html()
    assert 'interactive-widget=resizes-content' in html
    assert 'visualViewport' in html
    assert "setProperty('--kb'" in html or 'setProperty("--kb"' in html
    assert "setProperty('--inbox-nav'" not in html
    assert 'setProperty("--inbox-nav"' not in html


def test_conversacion_lleva_compositor_aviso_y_cascaron():
    entrante(CLI_ANA, texto='Hola, busco un apartamento')
    html = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    assert 'class="inbox-shell"' in html
    assert 'id="reply"' in html
    assert 'id="reply-nota"' in html
    assert 'Escribe una respuesta' in html or 'id="reply-text"' in html
    assert 'class="nota' in html
    assert 'class="inbox-nav-space"' not in html


def test_boton_atras_es_svg_centrado_y_mas_grande():
    """El ← de la conversación es un chevron SVG de 48 px, centrado con flex."""
    css = _css_panel()
    back = _regla(css, '.thread-head .back')
    assert 'width:48px' in back
    assert 'height:48px' in back
    assert 'align-items:center' in back
    assert 'justify-content:center' in back
    assert 'line-height:0' in back
    assert 'background:#f3f4f6' in back
    svg = _regla(css, '.thread-head .back svg')
    assert 'display:block' in svg
    active = _regla(css, '.thread-head .back:active,.thread-head .back.is-pressed')
    assert 'var(--naranja-suave)' in active
    head = _regla(css, '.thread-head')
    assert 'align-items:center' in head
    html = _panel_html()
    assert 'data-ui="composer-firme"' in html
    assert 'aria-label="Volver a la lista"' in html
    assert 'stroke-width="2.4"' in html
    assert 'M15 6 9 12l6 6' in html
    assert '←</a>' not in html


def test_conversacion_sigue_reserva_barra_y_aviso_24h():
    """No se esconde la barra inferior; el aviso de 24 h sigue sobre la caja."""
    css = _css_panel()
    assert '.inbox-shell' in css
    assert 'position:static' in css
    # La barra solo se oculta con el teclado, no al abrir la conversación.
    assert 'body.inbox-page.con .bottomnav{display:none}' not in css.replace(' ', '')
    entrante(CLI_ANA, texto='Hola, busco un apartamento')
    html = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    assert 'class="bottomnav"' in html
    assert 'id="reply"' in html
    assert 'id="reply-nota"' in html
    assert 'Ventana de 24 h' in html or 'id="reply-nota"' in html
    assert 'class="inbox-shell"' in html
