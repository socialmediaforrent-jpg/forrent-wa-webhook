#!/usr/bin/env python3
"""Capturas del compositor en Mensajes: rutas de apertura, antes y después."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(os.environ.get('FORRENT_ROOT') or Path(__file__).resolve().parents[1])
OUT = Path(__file__).resolve().parent
BOX = Path('/workspace/forrent/reports')
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
os.environ['SQLITE_PATH'] = os.path.join(_tmp, 'cap.db')
os.environ.pop('DATABASE_URL', None)
os.environ['PANEL_PASSWORD'] = 'clave-jose'
os.environ['SECRET_KEY'] = 'captura-composer-firme'
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
CLI_ROCIO = 'ig:rociohernandezrpinmobiliaria'
mensajes.clientes_por_telefono = lambda: {
    CLI_ANA: [{'id': '1', 'nombre': 'Andrea Gómez', 'asesor': 'ana',
               'asignado_at': '2026-11-10', 'en_ejercicio': True}],
    CLI_ROCIO: [{'id': '2', 'nombre': 'rociohernandezrpinmobiliaria', 'asesor': 'ana',
                 'asignado_at': '2026-11-10', 'en_ejercicio': True}],
}

NOW = datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc)


def ev(wa, name, text, canal='whatsapp', hours=1, direction='inbound'):
    ts = (NOW - timedelta(hours=hours, minutes=3)).isoformat()
    appmod.insert_event(
        kind='message' if direction == 'inbound' else 'outbound',
        wamid='ghl-cap-' + wa[:12] + str(hours) + direction,
        wa_id=wa, name=name, direction=direction, msg_type='text', text=text,
        ts=ts, status='delivered', source='ghl_sync',
        data={'channel': canal, 'canal': canal, 'ghl_contact_id': 'cid-' + wa[:12]},
    )


ev(CLI_ANA, 'Andrea Gómez', 'estaré atenta para cuando me contacten', 'whatsapp', 0.3)
ev(CLI_ANA, 'Andrea Gómez', 'Hola, quiero información del de 2 habitaciones', 'whatsapp', 8)
ev(CLI_ANA, 'Andrea Gómez', 'Claro, te confirmo disponibilidad y te escribo.', 'whatsapp', 7, direction='outbound')

# Hilo largo, tipo el de Instagram que José vio mal a las 5:39 PM.
for i in range(18):
    ev(CLI_ROCIO, 'rociohernandezrpinmobiliaria',
       f'Mensaje de prueba {i+1}: consulto por el local y la disponibilidad esta semana.',
       'instagram', hours=0.2 + i * 0.35,
       direction='inbound' if i % 2 == 0 else 'outbound')


MEASURE_JS = """() => {
  const reply = document.getElementById('reply');
  const btn = document.getElementById('reply-btn');
  const nota = document.getElementById('reply-nota');
  const nav = document.querySelector('.bottomnav');
  const shell = document.querySelector('.inbox-shell');
  const space = document.querySelector('.inbox-nav-space');
  const vv = window.visualViewport;
  const navSt = nav ? getComputedStyle(nav) : null;
  const navVisible = !!(nav && navSt && navSt.display !== 'none' && navSt.visibility !== 'hidden');
  const nr = navVisible ? nav.getBoundingClientRect() : null;
  const br = btn ? btn.getBoundingClientRect() : null;
  const rr = reply ? reply.getBoundingClientRect() : null;
  const nrNota = nota ? nota.getBoundingClientRect() : null;
  const layoutH = window.innerHeight;
  const visH = vv ? vv.height : layoutH;
  const visBottom = vv ? (vv.offsetTop + vv.height) : layoutH;
  const navTop = nr ? nr.top : visBottom;
  const navPos = nav ? getComputedStyle(nav).position : null;
  const limit = navVisible ? navTop : visBottom;
  const okBtn = !!(br && br.bottom <= limit + 0.5 && br.bottom <= visBottom + 0.5 && br.top >= 0);
  const okNota = !!(nrNota && nrNota.bottom <= limit + 0.5 && nrNota.bottom <= visBottom + 0.5);
  const okReply = !!(rr && rr.bottom <= limit + 0.5 && rr.bottom <= visBottom + 0.5);
  const body = getComputedStyle(document.body);
  const shellSt = shell ? getComputedStyle(shell) : null;
  return {
    innerHeight: layoutH,
    visualHeight: visH,
    visualBottom: visBottom,
    navVisible,
    navPos,
    nav: nr && {top: +nr.top.toFixed(2), bottom: +nr.bottom.toFixed(2), height: +nr.height.toFixed(2)},
    reply: rr && {top: +rr.top.toFixed(2), bottom: +rr.bottom.toFixed(2), height: +rr.height.toFixed(2)},
    btn: br && {top: +br.top.toFixed(2), bottom: +br.bottom.toFixed(2), height: +br.height.toFixed(2)},
    nota: nrNota && {top: +nrNota.top.toFixed(2), bottom: +nrNota.bottom.toFixed(2)},
    gapBtnNav: (br && nr) ? +(nr.top - br.bottom).toFixed(2) : null,
    gapReplyNav: (rr && nr) ? +(nr.top - rr.bottom).toFixed(2) : null,
    okBtn, okNota, okReply,
    bodyPos: body.position,
    bodyPadBottom: body.paddingBottom,
    shell: shellSt && {pos: shellSt.position, inset: shellSt.top, padBottom: shellSt.paddingBottom,
                       top: +shell.getBoundingClientRect().top.toFixed(2),
                       bottom: +shell.getBoundingClientRect().bottom.toFixed(2)},
    spaceH: space ? space.getBoundingClientRect().height : 0,
    inboxNav: document.documentElement.style.getPropertyValue('--inbox-nav'),
    kb: document.documentElement.style.getPropertyValue('--kb'),
    href: location.pathname + location.search,
  };
}"""


def configs(pw):
    pixel = pw.devices['Pixel 7']
    return [
        ('pixel7', dict(pixel)),
        ('473x1024', {
            'viewport': {'width': 473, 'height': 1024},
            'device_scale_factor': 2,
            'is_mobile': True,
            'has_touch': True,
            'user_agent': ('Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36'),
            'screen': {'width': 473, 'height': 1120},
        }),
    ]


def convo_sel(wa_id):
    return f'a.convo[href*="{quote(wa_id, safe="")}"]'


def open_routes(page, port, route, wa_id):
    base = f'http://127.0.0.1:{port}'
    enc = quote(wa_id, safe='')
    sel = convo_sel(wa_id)
    if route == 'url':
        page.goto(f'{base}/panel?c={enc}', wait_until='networkidle')
    elif route == 'lista':
        page.goto(f'{base}/panel', wait_until='networkidle')
        page.wait_for_selector(sel)
        page.click(sel)
        page.wait_for_selector('#reply-btn')
    elif route == 'atras':
        page.goto(f'{base}/panel?c={enc}', wait_until='networkidle')
        page.wait_for_selector('.thread-head .back')
        page.click('.thread-head .back')
        page.wait_for_selector(sel)
        page.click(sel)
        page.wait_for_selector('#reply-btn')
    elif route == 'modulos':
        page.goto(f'{base}/panel?c={enc}', wait_until='networkidle')
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
    page.wait_for_selector('#reply-btn')
    page.wait_for_timeout(400)


MISMATCH_JS = """() => {
  const extra = 96;
  const h = (window.innerHeight + extra) + 'px';
  document.documentElement.style.height = h;
  document.documentElement.style.maxHeight = h;
  document.body.style.height = h;
  document.body.style.maxHeight = h;
}"""


def run(tag, routes=None, only_rocio=False, mismatch=False):
    if routes is None:
        routes = ['url'] if tag == 'antes' else ['url', 'lista', 'atras', 'modulos']
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
    measures = []
    wa_id = CLI_ROCIO if only_rocio or True else CLI_ANA
    # Siempre usamos el hilo largo (el caso que José vio mal).
    wa_id = CLI_ROCIO
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='/usr/bin/google-chrome',
            args=['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
        )
        for name, opts in configs(pw):
            ctx_kwargs = {k: v for k, v in opts.items() if k in (
                'viewport', 'device_scale_factor', 'is_mobile', 'has_touch',
                'user_agent', 'screen', 'default_browser_type')}
            for route in routes:
                ctx = browser.new_context(**ctx_kwargs)
                ctx.add_cookies([{'name': 'forrent_session', 'value': ck.value,
                                  'domain': '127.0.0.1', 'path': '/'}])
                page = ctx.new_page()
                page.add_init_script('try{sessionStorage.clear()}catch(e){}')
                open_routes(page, port, route, wa_id)
                if mismatch:
                    page.evaluate(MISMATCH_JS)
                    page.wait_for_timeout(80)
                m = page.evaluate(MEASURE_JS)
                m['device'] = name
                m['route'] = route
                m['tag'] = tag
                m['mismatch'] = bool(mismatch)
                measures.append(m)
                suffix = f'{route}_dvh' if mismatch else route
                dest = OUT / f'composer_firme_{tag}_{name}_{suffix}.png'
                page.screenshot(path=str(dest), full_page=False)
                outs.append(dest)
                ctx.close()
        browser.close()

    server.shutdown()
    BOX.mkdir(parents=True, exist_ok=True)
    for f in outs:
        target = BOX / f.name
        target.write_bytes(f.read_bytes())
        print('wrote', f.name, 'bytes', f.stat().st_size)
    meas_path = OUT / f'composer_firme_{tag}_medidas.json'
    meas_path.write_text(json.dumps(measures, indent=2, ensure_ascii=False), encoding='utf-8')
    (BOX / meas_path.name).write_bytes(meas_path.read_bytes())
    print('medidas', meas_path)
    failed = [m for m in measures if not (m.get('okBtn') and m.get('okNota') and m.get('okReply'))]
    for m in measures:
        print(json.dumps(m, ensure_ascii=False))
    if failed:
        print('FALLO medidas en', [(m['device'], m['route']) for m in failed])
        return 1
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--tag', default='despues', choices=['antes', 'despues'])
    p.add_argument('--routes', default='')
    p.add_argument('--mismatch', action='store_true')
    args = p.parse_args()
    routes = [r for r in args.routes.split(',') if r] or None
    sys.exit(run(args.tag, routes=routes, mismatch=args.mismatch))
