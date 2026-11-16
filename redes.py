"""Redes Sociales de For Rent (sección de Mercadeo). Solo enlaces reales; casilla vacía si no hay enlace."""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data' / 'redes_sociales.json'

REDES = [
    {'key': 'instagram', 'nombre': 'Instagram', 'logo': 'redes/instagram.svg', 'dominios': ('instagram.com',)},
    {'key': 'facebook', 'nombre': 'Facebook', 'logo': 'redes/facebook.svg', 'dominios': ('facebook.com', 'fb.com')},
    {'key': 'tiktok', 'nombre': 'TikTok', 'logo': 'redes/tiktok.svg', 'dominios': ('tiktok.com',)},
    {'key': 'youtube', 'nombre': 'YouTube', 'logo': 'redes/youtube.svg', 'dominios': ('youtube.com',)},
    {'key': 'linkedin', 'nombre': 'LinkedIn', 'logo': 'redes/linkedin.svg', 'dominios': ('linkedin.com',)},
]


def _host_ok(url, dominios):
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = (u.hostname or '').lower()
    return u.scheme == 'https' and any(host == d or host.endswith('.' + d) for d in dominios)


def _config():
    raw = os.environ.get('REDES_SOCIALES_JSON')
    if raw:
        try:
            d = json.loads(raw)
            return d.get('redes', d) if isinstance(d, dict) else {}
        except Exception:
            pass
    try:
        return json.loads(DATA.read_text(encoding='utf-8')).get('redes') or {}
    except Exception:
        return {}


def redes():
    """Lista de 5 casillas: url solo si es https y del dominio de esa red (si no, casilla vacía)."""
    cfg = _config()
    out = []
    for r in REDES:
        url = str(cfg.get(r['key']) or '').strip()
        if url and not _host_ok(url, r['dominios']):
            url = ''
        out.append({**r, 'url': url, 'visible': bool(url)})
    return out
