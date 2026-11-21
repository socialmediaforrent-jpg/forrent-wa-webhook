#!/usr/bin/env python3
"""Capturas y medidas del compositor y del botón atrás en Mensajes (Android)."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
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
os.environ['SECRET_KEY'] = 'captura-composer2'
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
    CLI_ANA: [{'id': '1', 'nombre': 'Andrea Gómez', 'asesor': 'ana',
               'asignado_at': '2026-11-10', 'en_ejercicio': True}],
}

NOW = datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc)


def ev(wa, name, text, canal='whatsapp', hours=1, direction='inbound'):
    ts = (NOW - timedelta(hours=hours, minutes=3)).isoformat()
    appmod.insert_event(
        kind='message' if direction == 'inbound' else 'outbound',
        wamid='ghl-cap-' + wa + str(hours) + direction,
        wa_id=wa, name=name, direction=direction, msg_type='text', text=text,
        ts=ts, status='delivered', source='ghl_sync',
        data={'channel': canal, 'canal': canal, 'ghl_contact_id': 'cid-' + wa[:12]},
    )


ev(CLI_ANA, 'Andrea Gómez', 'estaré atenta para cuando me contacten', 'whatsapp', 0.3)
ev(CLI_ANA, 'Andrea Gómez', 'Hola, quiero información del de 2 habitaciones', 'whatsapp', 8)
ev(CLI_ANA, 'Andrea Gómez', 'Claro, te confirmo disponibilidad y te escribo.', 'whatsapp', 7, direction='outbound')


MEASURE_JS = """() => {
  const reply = document.getElementById('reply');
  const btn = document.getElementById('reply-btn');
  const nota = document.getElementById('reply-nota');
  const nav = document.querySelector('.bottomnav');
  const back = document.querySelector('.thread-head .back');
  const head = document.querySelector('.thread-head');
  const av = document.querySelector('.thread-head .av');
  const who = document.querySelector('.thread-head .who');
  const vv = window.visualViewport;
  const navSt = nav ? getComputedStyle(nav) : null;
  const navVisible = !!(nav && navSt && navSt.display !== 'none' && navSt.visibility !== 'hidden');
  const nr = navVisible ? nav.getBoundingClientRect() : null;
  const br = btn ? btn.getBoundingClientRect() : null;
  const rr = reply ? reply.getBoundingClientRect() : null;
  const nrNota = nota ? nota.getBoundingClientRect() : null;
  const backR = back ? back.getBoundingClientRect() : null;
  const avR = av ? av.getBoundingClientRect() : null;
  const whoR = who ? who.getBoundingClientRect() : null;
  const svg = back ? back.querySelector('svg') : null;
  const svgR = svg ? svg.getBoundingClientRect() : null;
  const layoutH = window.innerHeight;
  const visH = vv ? vv.height : layoutH;
  const visBottom = vv ? (vv.offsetTop + vv.height) : layoutH;
  const navTop = nr ? nr.top : visBottom;
  const limit = navVisible ? navTop : visBottom;
  const okBtn = !!(br && br.bottom <= limit + 0.5 && br.bottom <= visBottom + 0.5 && br.top >= 0);
  const okNota = !!(nrNota && nrNota.bottom <= limit + 0.5);
  const okReply = !!(rr && rr.bottom <= visBottom + 0.5);
  let backCentered = null;
  if (backR && svgR) {
    const cx = backR.left + backR.width / 2;
    const cy = backR.top + backR.height / 2;
    const sx = svgR.left + svgR.width / 2;
    const sy = svgR.top + svgR.height / 2;
    backCentered = {dx: +(sx - cx).toFixed(2), dy: +(sy - cy).toFixed(2),
                    ok: Math.abs(sx - cx) <= 1.5 && Math.abs(sy - cy) <= 1.5};
  }
  return {
    innerHeight: layoutH,
    visualHeight: visH,
    visualBottom: visBottom,
    navVisible,
    nav: nr && {top: +nr.top.toFixed(2), bottom: +nr.bottom.toFixed(2), height: +nr.height.toFixed(2)},
    reply: rr && {top: +rr.top.toFixed(2), bottom: +rr.bottom.toFixed(2)},
    btn: br && {top: +br.top.toFixed(2), bottom: +br.bottom.toFixed(2), height: +br.height.toFixed(2)},
    nota: nrNota && {top: +nrNota.top.toFixed(2), bottom: +nrNota.bottom.toFixed(2)},
    okBtn, okNota, okReply,
    back: backR && {w: +backR.width.toFixed(2), h: +backR.height.toFixed(2),
                    top: +backR.top.toFixed(2), left: +backR.left.toFixed(2),
                    display: getComputedStyle(back).display,
                    lineHeight: getComputedStyle(back).lineHeight,
                    text: (back.innerText || '').trim(),
                    hasSvg: !!svg},
    backCentered,
    av: avR && {w: +avR.width.toFixed(2), h: +avR.height.toFixed(2),
                cy: +(avR.top + avR.height/2).toFixed(2)},
    who: whoR && {cy: +(whoR.top + whoR.height/2).toFixed(2)},
  };
}"""


def crop_back(page, dest):
    loc = page.locator('.thread-head')
    loc.wait_for()
    loc.screenshot(path=str(dest))
    return dest


def run(tag):
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
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='/usr/bin/google-chrome',
            args=['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
        )
        configs = []
        pixel = pw.devices['Pixel 7']
        configs.append(('pixel7', dict(pixel)))
        s9 = pw.devices['Galaxy S9+']
        configs.append(('s9plus', dict(s9)))
        configs.append(('473x1024', {
            'viewport': {'width': 473, 'height': 1024},
            'device_scale_factor': 2,
            'is_mobile': True,
            'has_touch': True,
            'user_agent': ('Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36'),
            'screen': {'width': 473, 'height': 1120},
        }))

        for name, opts in configs:
            ctx_kwargs = {k: v for k, v in opts.items() if k in (
                'viewport', 'device_scale_factor', 'is_mobile', 'has_touch',
                'user_agent', 'screen', 'default_browser_type')}
            ctx = browser.new_context(**ctx_kwargs)
            ctx.add_cookies([{'name': 'forrent_session', 'value': ck.value,
                              'domain': '127.0.0.1', 'path': '/'}])
            page = ctx.new_page()
            page.add_init_script('try{sessionStorage.clear()}catch(e){}')
            if name.endswith('chromeui'):
                page.add_init_script("""
                  (() => {
                    const bar = () => {
                      if (document.getElementById('fake-chrome-bar')) return;
                      const el = document.createElement('div');
                      el.id = 'fake-chrome-bar';
                      el.style.cssText = 'position:fixed;left:0;right:0;top:0;height:56px;z-index:9999;'
                        + 'background:#202124;color:#e8eaed;font:12px/56px Roboto,Arial,sans-serif;'
                        + 'padding:0 12px;box-sizing:border-box;pointer-events:none';
                      el.textContent = 'online.grupoinmobiliarioforrent.com/panel';
                      document.documentElement.style.setProperty('padding-top', '56px');
                      document.body && document.body.insertAdjacentElement('afterbegin', el);
                    };
                    document.addEventListener('DOMContentLoaded', bar);
                  })();
                """)
            page.goto(f'http://127.0.0.1:{port}/panel?c={CLI_ANA}', wait_until='networkidle')
            page.wait_for_selector('#reply-btn')
            page.wait_for_timeout(400)
            if name.endswith('chromeui') and not page.locator('#fake-chrome-bar').count():
                page.evaluate("""() => {
                  const el = document.createElement('div');
                  el.id = 'fake-chrome-bar';
                  el.style.cssText = 'position:fixed;left:0;right:0;top:0;height:56px;z-index:9999;'
                    + 'background:#202124;color:#e8eaed;font:12px/56px Roboto,Arial,sans-serif;'
                    + 'padding:0 12px;box-sizing:border-box;pointer-events:none';
                  el.textContent = 'online.grupoinmobiliarioforrent.com/panel';
                  document.body.insertAdjacentElement('afterbegin', el);
                }""")
                page.wait_for_timeout(50)
            m = page.evaluate(MEASURE_JS)
            m['device'] = name
            m['tag'] = tag
            measures.append(m)
            dest = OUT / f'composer2_{tag}_{name}.png'
            page.screenshot(path=str(dest), full_page=False)
            outs.append(dest)
            crop = OUT / f'composer2_{tag}_{name}_atras.png'
            crop_back(page, crop)
            outs.append(crop)
            ctx.close()
        browser.close()

    server.shutdown()
    BOX.mkdir(parents=True, exist_ok=True)
    for f in outs:
        target = BOX / f.name
        target.write_bytes(f.read_bytes())
        print('wrote', f.name, 'bytes', f.stat().st_size)
    meas_path = OUT / f'composer2_{tag}_medidas.json'
    meas_path.write_text(json.dumps(measures, indent=2, ensure_ascii=False), encoding='utf-8')
    (BOX / meas_path.name).write_bytes(meas_path.read_bytes())
    print('medidas', meas_path)
    failed = [m for m in measures if not (m.get('okBtn') and m.get('okNota') and m.get('okReply'))]
    for m in measures:
        print(json.dumps(m, ensure_ascii=False))
    if failed:
        print('FALLO medidas en', [m['device'] for m in failed])
        return 1
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--tag', default='despues', choices=['antes', 'despues'])
    args = p.parse_args()
    sys.exit(run(args.tag))
