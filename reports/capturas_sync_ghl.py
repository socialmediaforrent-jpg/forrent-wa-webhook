#!/usr/bin/env python3
"""Capturas 473×1024 de Mensajes (tarjetas, canales y pull-to-refresh). Datos de prueba."""
import hashlib, json, os, sys, tempfile, threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
BOX = Path('/workspace/forrent/reports')
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
os.environ['SQLITE_PATH'] = os.path.join(_tmp, 'cap.db')
os.environ.pop('DATABASE_URL', None)
os.environ['PANEL_PASSWORD'] = 'clave-jose'
os.environ['SECRET_KEY'] = 'captura-sync-ghl'
os.environ['MERCADEO_PASSWORD'] = 'clave-mercadeo'
os.environ['PORTAL_ASESORES_SHA256'] = json.dumps({'ana': hashlib.sha256(b'clave-ana').hexdigest()})
os.environ['GHL_SYNC_DISABLED'] = '1'
os.environ['GHL_API_TOKEN'] = 'captura'
os.environ['GHL_LOCATION_ID'] = 'captura'
for k in ('META_WA_TOKEN', 'WA_PHONE_NUMBER_ID', 'META_SOCIAL_INBOX',
          'WA_SEND_PROVIDER', 'RENDER'):
    os.environ.pop(k, None)
os.chdir(_tmp)

import app as appmod  # noqa: E402
import mensajes  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

CLI_ANA = '584121111111'
mensajes.clientes_por_telefono = lambda: {
    CLI_ANA: [{'id': '1', 'nombre': 'Carla Test', 'asesor': 'ana', 'asignado_at': '2026-11-10', 'en_ejercicio': True}],
}

NOW = datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc)


def ev(wa, name, text, canal, hours=1, direction='inbound'):
    ts = (NOW - timedelta(hours=hours, minutes=3)).isoformat()
    appmod.insert_event(
        kind='message' if direction == 'inbound' else 'outbound',
        wamid='ghl-cap-' + wa + str(hours) + direction,
        wa_id=wa, name=name, direction=direction, msg_type='text', text=text,
        ts=ts, status='delivered', source='ghl_sync',
        data={'channel': canal, 'canal': canal, 'ghl_contact_id': 'cid-' + wa[:12]},
    )


ev(CLI_ANA, 'Carla Test', 'Hola, ¿sigue disponible el de Los Palos Grandes?', 'whatsapp', 2)
ev(CLI_ANA, 'Carla Test', 'Sí, te confirmo precio y visita.', 'whatsapp', 1.8, direction='outbound')
ev('ig:cidLuna', 'luna.demo', '¿El apto del reel está libre en diciembre?', 'instagram', 0.4)
ev('fb:cidFb', 'fb.demo.perez', 'Vi el local en Facebook, ¿puedo ir mañana?', 'facebook', 1.1)
ev('email:demo@forrent.test', 'demo@forrent.test', 'Asunto: visita a Altamira', 'email', 3)
ev('sms:584123333333', 'Luis Demo', 'Gracias por la info', 'sms', 4)
ev('584129999888', 'Andrés Demo', 'Buenas tardes, busco 2 habitaciones', 'whatsapp', 5)
ev('ig:cidVal', 'valery.demo', 'Me interesa el de Catia', 'instagram', 0.2)

# más mensajes en el hilo de IG para la captura
ev('ig:cidLuna', 'luna.demo', 'Quedó muy lindo en el video.', 'instagram', 0.5)
ev('ig:cidLuna', 'luna.demo', 'Claro, te ayudo con las fechas.', 'instagram', 0.35, direction='outbound')


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
        ctx = browser.new_context(viewport={'width': 473, 'height': 1024}, device_scale_factor=2,
                                  user_agent='Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/122.0.0.0 Mobile Safari/537.36')
        ctx.add_cookies([{'name': 'forrent_session', 'value': ck.value, 'domain': '127.0.0.1', 'path': '/'}])
        page = ctx.new_page()
        page.add_init_script('try{sessionStorage.clear()}catch(e){}')
        page.goto(f'http://127.0.0.1:{port}/panel', wait_until='networkidle')
        page.wait_for_selector('a.convo')
        page.wait_for_timeout(400)
        dest = OUT / 'sync_ghl_lista_tarjetas_473x1024.png'
        page.screenshot(path=str(dest), full_page=False)
        outs.append(dest)
        page.locator('a.convo', has_text='luna.demo').click()
        page.wait_for_selector('.bub')
        page.wait_for_timeout(300)
        dest = OUT / 'sync_ghl_hilo_instagram_473x1024.png'
        page.screenshot(path=str(dest), full_page=False)
        outs.append(dest)
        back = page.locator('.thread-head .back')
        if back.count():
            back.click()
        page.wait_for_selector('a.convo')
        page.wait_for_timeout(200)
        page.evaluate("""() => {
          const t = window.__inboxPtr;
          document.querySelector('.inbox-list').classList.add('ptr-loading');
          t.ptrSet(80);
          t.showSkeleton(7);
        }""")
        page.wait_for_selector('.convo.skeleton')
        page.wait_for_timeout(350)
        dest = OUT / 'sync_ghl_pull_refresh_473x1024.png'
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
