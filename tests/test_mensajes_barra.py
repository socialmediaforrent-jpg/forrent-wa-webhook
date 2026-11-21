"""La barra del cliente en Mensajes se queda visible al bajar la conversación."""
import os
import re

import pytest

from test_bandeja import CLI_ANA, ROOT, cliente, entrante  # noqa: F401  (configura el entorno de prueba)
import app as appmod  # noqa: E402  (ya configurado por test_bandeja)


@pytest.fixture(autouse=True)
def _limpiar_eventos():
    appmod.q('DELETE FROM wa_events')
    yield


def _css_panel():
    ruta = os.path.join(ROOT, 'templates', 'panel.html')
    html = open(ruta, encoding='utf-8').read()
    m = re.search(r'<style[^>]*>(.*?)</style>', html, re.S)
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
    assert 'class="back"' in html or 'class="back "' in html
    assert 'aria-label="Volver a la lista"' in html
    assert '<svg' in html and 'M15 6' in html
    assert 'Escribe una respuesta' in html or 'id="reply"' in html



def test_css_burbuja_no_se_sale():
    css = _css_panel()
    bub = _regla(css, '.bub')
    assert 'overflow-wrap:anywhere' in bub
    assert 'word-break:break-word' in bub
    assert 'min-width:0' in bub
    assert 'max-width:min(520px,86%)' in bub
    txt = _regla(css, '.bub .txt,.bub .txt a')
    assert 'overflow-wrap:anywhere' in txt
    assert 'word-break:break-word' in txt
    foto = _regla(css, '.bub .foto')
    assert 'max-width:100%' in foto
    assert 'height:auto' in foto
    assert 'max-height:240px' in foto


def test_enlaza_http_https_y_www():
    html = appmod.texto_con_enlaces('Mira https://example.com/foto y www.forrent.com/x y HTTP://ok.example/a.')
    assert 'href="https://example.com/foto"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'href="https://www.forrent.com/x"' in html
    assert 'href="HTTP://ok.example/a"' in html
    assert html.endswith('.')
    assert '&lt;' not in html


def test_xss_se_escapa_y_javascript_no_es_enlace():
    html = appmod.texto_con_enlaces(
        '<script>alert(1)</script> javascript:alert(1) <img src=x onerror=alert(1)> '
        'https://ok.example/a" onclick="alert(1)'
    )
    assert '<script>' not in html
    assert '<img' not in html
    assert 'href="javascript:' not in html.lower()
    assert '&lt;script&gt;' in html
    assert '&lt;img' in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert 'href="https://ok.example/a"' in html
    assert 'onclick=&quot;alert(1)' in html
    assert 'onclick="alert' not in html


def test_burbuja_renderiza_enlace_y_escapa_xss():
    entrante(CLI_ANA, texto='Mira https://forrent.example/apto y <script>alert(1)</script>')
    pagina = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    m = re.search(r'<div class="txt">(.*?)</div>', pagina, re.S)
    assert m, 'falta el texto de la burbuja'
    txt = m.group(1)
    assert 'href="https://forrent.example/apto"' in txt
    assert 'target="_blank"' in txt and 'rel="noopener noreferrer"' in txt
    assert '&lt;script&gt;' in txt
    assert '<script>alert' not in txt


def test_imagen_muestra_miniatura_y_no_la_url():
    url = 'https://static-assets.msgsndr.com/abc/foto-muy-larga-del-inmueble.jpg'
    ts = '2026-11-21T12:00:00+00:00'
    appmod.insert_event(kind='message', wamid='in-img-1', wa_id=CLI_ANA, name='Cliente',
                        direction='inbound', msg_type='image', text='[imagen] ' + url,
                        ts=ts, status='', source='ghl_import', data={'url': url})
    pagina = cliente('jose').get('/panel?c=' + CLI_ANA).get_data(as_text=True)
    assert f'src="{url}"' in pagina
    assert f'href="{url}"' in pagina
    assert 'loading="lazy"' in pagina
    assert 'Ver imagen' in pagina
    assert 'target="_blank"' in pagina
    assert 'rel="noopener noreferrer"' in pagina
    parte = pagina.split('<section class="inbox-thread">', 1)[-1]
    hilo = parte.split('</section>', 1)[0]
    txt = re.search(r'<div class="txt">(.*?)</div>', hilo, re.S)
    assert not txt or url not in txt.group(1)
    # El [imagen] no debe verse en la burbuja. Puede seguir en el JSON de
    # precarga de la lista (inbox-boot), que está fuera de esta sección.
    assert '[imagen]' not in hilo.lower()
    tipo = re.search(r'<div class="tipo">(.*?)</div>', hilo, re.S)
    assert not tipo or tipo.group(1) != 'Imagen'
    assert 'class="foto"' in hilo
