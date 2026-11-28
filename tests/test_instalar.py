"""Página pública /instalar: PWA, manifest y service worker sin caché."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_bandeja import appmod  # noqa: E402


def test_instalar_es_publica_sin_cache():
    r = appmod.app.test_client().get('/instalar')
    assert r.status_code == 200
    assert 'no-store' in (r.headers.get('Cache-Control') or '')
    html = r.get_data(as_text=True)
    assert 'Instalar For Rent Online' in html
    assert 'logo-blanco' in html
    assert 'serviceWorker.register' in html and "'/sw.js'" in html
    assert 'beforeinstallprompt' in html
    assert 'Agregar a inicio' in html
    assert 'Instalar app' in html and 'Agregar a la pantalla principal' in html
    assert 'Activar notificaciones' in html
    assert 'display-mode: standalone' in html
    assert 'Ya tienes For Rent Online' in html
    assert 'href="/login"' in html
    assert 'manifest.webmanifest' in html


def test_scripts_de_instalar_son_javascript_valido(tmp_path):
    if not shutil.which('node'):
        pytest.skip('sin node')
    html = appmod.app.test_client().get('/instalar').get_data(as_text=True)
    scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
    assert any('beforeinstallprompt' in s and "register('/sw.js'" in s for s in scripts)
    for i, js in enumerate(scripts):
        f = tmp_path / f's{i}.js'
        f.write_text(js)
        r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_manifest_cumple_instalacion_chrome():
    r = appmod.app.test_client().get('/static/manifest.webmanifest')
    assert r.status_code == 200
    m = r.get_json()
    if m is None:
        m = json.loads(r.get_data(as_text=True))
    assert m['name'] == 'For Rent Online'
    assert m['short_name']
    assert m['start_url']
    assert m['display'] == 'standalone'
    sizes = {i['sizes']: i['src'] for i in m['icons']}
    assert '192x192' in sizes and sizes['192x192'].endswith('icon-192.png')
    assert any(i['sizes'] == '512x512' and i['src'].endswith('icon-512.png') for i in m['icons'])
    for name in ('icon-192.png', 'icon-512.png'):
        ir = appmod.app.test_client().get(f'/static/{name}')
        assert ir.status_code == 200
        assert ir.data[:8] == b'\x89PNG\r\n\x1a\n'


def test_login_tiene_enlace_a_instalar():
    html = appmod.app.test_client().get('/login').get_data(as_text=True)
    assert 'href="/instalar"' in html
