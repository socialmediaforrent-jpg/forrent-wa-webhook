#!/usr/bin/env python3
"""Capturas 473×1024 de Módulos: los 5 contraídos, y uno abierto al tocarlo."""
import hashlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
BOX = Path('/workspace/forrent/reports')
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
os.environ['SQLITE_PATH'] = os.path.join(_tmp, 'cap.db')
os.environ.pop('DATABASE_URL', None)
os.environ['PANEL_PASSWORD'] = 'clave-jose'
os.environ['SECRET_KEY'] = 'captura-modulos-contraidos'
os.environ['MERCADEO_PASSWORD'] = 'clave-mercadeo'
os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest()})
os.environ['GHL_SYNC_DISABLED'] = '1'
for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX',
          'WA_SEND_PROVIDER', 'RENDER', 'GHL_API_TOKEN', 'GHL_LOCATION_ID'):
    os.environ.pop(k, None)
os.chdir(_tmp)

import app as appmod  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def run():
    server = make_server('127.0.0.1', 0, appmod.app)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s['uid'] = 'jose'
    ck = c.get_cookie('forrent_session', domain='localhost') or c.get_cookie('session')
    if ck is None:
        raise SystemExit('no session cookie')
    from playwright.sync_api import sync_playwright
    outs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome',
                                     args=['--no-sandbox', '--disable-gpu', '--hide-scrollbars'])
        ctx = browser.new_context(
            viewport={'width': 473, 'height': 1024},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent=('Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36'),
        )
        ctx.add_cookies([{'name': 'forrent_session', 'value': ck.value,
                          'domain': '127.0.0.1', 'path': '/'}])
        page = ctx.new_page()
        page.add_init_script("""
          try { localStorage.clear(); } catch (e) {}
          try { sessionStorage.clear(); } catch (e) {}
          if (location.hash) location.hash = '';
        """)
        page.goto(f'http://127.0.0.1:{port}/portal', wait_until='domcontentloaded')
        page.wait_for_selector('nav.bottomnav')
        page.locator('nav.bottomnav a', has_text='Módulos').click()
        page.wait_for_url('**/modulos')
        page.wait_for_selector('details.mod#mod-inventario')
        page.wait_for_timeout(400)
        estado = page.evaluate("""() => [...document.querySelectorAll('details.mod')].map(d => ({
          id: d.id, open: d.open, text: (d.querySelector('b') || {}).textContent || ''
        }))""")
        if len(estado) != 5 or any(x['open'] for x in estado):
            raise SystemExit(f'módulos no salieron contraídos: {estado}')
        dest = OUT / 'modulos_contraidos_todos_473x1024.png'
        page.screenshot(path=str(dest), full_page=False)
        outs.append(dest)
        page.locator('details.mod#mod-inventario summary').click()
        page.wait_for_function("() => document.querySelector('details.mod#mod-inventario').open === true")
        page.wait_for_timeout(250)
        estado2 = page.evaluate("""() => [...document.querySelectorAll('details.mod')].map(d => ({
          id: d.id, open: d.open
        }))""")
        if not estado2[0]['open'] or any(x['open'] for x in estado2[1:]):
            raise SystemExit(f'al tocar Inventario el estado quedó: {estado2}')
        dest = OUT / 'modulos_contraidos_inventario_abierto_473x1024.png'
        page.screenshot(path=str(dest), full_page=False)
        outs.append(dest)
        browser.close()
    server.shutdown()
    BOX.mkdir(parents=True, exist_ok=True)
    for f in outs:
        target = BOX / f.name
        target.write_bytes(f.read_bytes())
        print('wrote', f, 'and', target, 'bytes', f.stat().st_size)
    return outs


if __name__ == '__main__':
    run()
