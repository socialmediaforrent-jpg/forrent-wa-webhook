"""La barra del cliente en Mensajes se queda visible al bajar la conversación."""
import os
import re

from test_bandeja import CLI_ANA, ROOT, cliente, entrante  # noqa: F401  (configura el entorno de prueba)


def _css_panel():
    ruta = os.path.join(ROOT, 'templates', 'panel.html')
    html = open(ruta, encoding='utf-8').read()
    m = re.search(r'<style>(.*?)</style>', html, re.S)
    assert m, 'falta el CSS de Mensajes'
    return m.group(1)


def _regla(css, selector):
    m = re.search(re.escape(selector) + r'\{[^}]+\}', css)
    assert m, f'falta la regla {selector}'
    return m.group(0)


def test_css_barra_del_cliente_se_queda_arriba():
    """La página no se desplaza: solo se mueven los mensajes, debajo de la barra."""
    css = _css_panel()
    html_css = _regla(css, 'html:has(body.inbox-page)')
    body = _regla(css, 'body.inbox-page')
    inbox = _regla(css, '.inbox')
    assert 'overflow:hidden' in html_css and '100dvh' in html_css
    assert 'overflow:hidden' in body and '100dvh' in body
    assert 'overflow:hidden' in inbox and 'flex:1' in inbox
    # El alto ya no se calcula a mano: el flex llena lo que queda bajo el encabezado.
    assert '100dvh - var(--appbar)' not in css

    head = _regla(css, '.thread-head')
    assert 'position:sticky' in head
    assert 'top:0' in head
    assert 'background:#fff' in head
    assert 'flex:none' in head
    assert 'z-index:3' in head

    msgs = _regla(css, '.msgs')
    assert 'overflow:auto' in msgs
    assert 'min-height:0' in msgs
    assert 'flex:1' in msgs

    reply = _regla(css, '.reply')
    assert 'flex:none' in reply


def test_conversacion_lleva_barra_del_cliente():
    entrante(CLI_ANA, texto='Hola, busco un apartamento')
    html = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    assert 'class="thread-head"' in html
    assert 'Cliente Ana' in html or 'Cliente' in html
    assert 'id="msgs"' in html
    assert 'class="inbox-page con"' in html
    assert '←' in html  # botón atrás en móvil
    assert 'Escribe una respuesta' in html or 'id="reply"' in html
