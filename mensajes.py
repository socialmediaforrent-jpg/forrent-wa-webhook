"""Bandeja de Mensajes: agrupación, corte 31-oct y visibilidad por asesor (teléfono)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from corte_clientes import en_ejercicio, fecha_de, CORTE_FECHA

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
REG_CANDIDATES = [
    os.environ.get('REGISTRO_JSON') or '',
    '/workspace/forrent/seguimiento_tg/registro.json',
    str(DATA / 'registro.json'),
]
TELEFONOS_SIDECAR = DATA / 'clientes_telefonos.json'

CANALES = {
    'whatsapp': 'WhatsApp',
    'instagram': 'Instagram',
    'facebook': 'Facebook',
    'email': 'Email',
    'sms': 'SMS',
    'messenger': 'Facebook',
    'ig': 'Instagram',
    'fb': 'Facebook',
    'wa': 'WhatsApp',
    'meta': 'WhatsApp',
}

AVATAR_COLORS = (
    '#0A5C36', '#1D4ED8', '#7C3AED', '#BE123C', '#C2410C',
    '#0F766E', '#A21CAF', '#0369A1', '#4338CA', '#B45309',
)


def norm_phone(s):
    """Normaliza un teléfono o wa_id a dígitos con código de país (VE 58)."""
    d = ''.join(c for c in str(s or '') if c.isdigit())
    if d.startswith('00'):
        d = d[2:]
    if d.startswith('580') and len(d) >= 12:
        d = '58' + d[3:]
    elif d.startswith('0') and len(d) >= 11:
        d = '58' + d.lstrip('0')
    elif len(d) == 10 and d.startswith('4'):
        d = '58' + d
    return d


def _iter_client_phone_rows():
    """Filas con id, nombre, asesor, asignado_at, telefonos. No borra nada."""
    rows = []
    try:
        import portal_lib as _pl
        if _pl._LIVE_LOADER is not None:
            live = _pl._LIVE_LOADER('clientes_telefonos.json')
            if live:
                rows = list(live.get('clientes') or [])
    except Exception:
        rows = []
    if not rows and TELEFONOS_SIDECAR.exists():
        try:
            data = json.loads(TELEFONOS_SIDECAR.read_text(encoding='utf-8'))
            for c in (data.get('clientes') or []):
                rows.append(c)
        except Exception:
            pass
    if not rows:
        for p in REG_CANDIDATES:
            if p and Path(p).exists():
                try:
                    cl = (json.loads(Path(p).read_text(encoding='utf-8')).get('clientes') or {})
                    if isinstance(cl, dict):
                        for k, c in cl.items():
                            rows.append({
                                'id': k,
                                'nombre': c.get('nombre') or '',
                                'asesor': c.get('asesor') or '',
                                'asignado_at': c.get('asignado_at') or '',
                                'telefono': c.get('telefono') or '',
                                'telefono_alt': c.get('telefono_alt') or '',
                            })
                except Exception:
                    rows = []
                break
    # asignados snapshot may later include telefono
    try:
        asig = json.loads((DATA / 'clientes_asignados.json').read_text(encoding='utf-8'))
        for c in (asig.get('clientes') or []):
            if c.get('telefono') or c.get('telefono_alt'):
                rows.append({
                    'id': c.get('id'),
                    'nombre': c.get('nombre') or '',
                    'asesor': c.get('asesor') or '',
                    'asignado_at': c.get('asignado_at') or '',
                    'telefono': c.get('telefono') or '',
                    'telefono_alt': c.get('telefono_alt') or '',
                })
    except Exception:
        pass
    return rows


def _asesor_slug(nombre_o_slug, portal_lib=None):
    s = (nombre_o_slug or '').strip()
    if not s:
        return ''
    low = s.lower()
    if portal_lib is None:
        import portal_lib as portal_lib  # noqa: F401
    for a in portal_lib.asesores_meta():
        if a['user'] == low or (a.get('name') or '').lower() == low:
            return a['user']
        # first-name unique?
    import unicodedata
    def nrm(x):
        return ''.join(ch for ch in unicodedata.normalize('NFD', x or '') if unicodedata.category(ch) != 'Mn').lower().strip()
    ns = nrm(s)
    for a in portal_lib.asesores_meta():
        if nrm(a.get('name')) == ns or nrm(a['user']) == ns:
            return a['user']
        if nrm(a.get('name')).startswith(ns + ' ') or ns.startswith(nrm(a.get('name')).split(' ')[0]):
            # weak; prefer exact
            pass
    # map known display names from asignados 'asesor' field (often slug already)
    return low


def clientes_por_telefono():
    """norm_phone -> lista de clientes (incluye anteriores al corte, para ocultarlos)."""
    import portal_lib
    out = {}
    for c in _iter_client_phone_rows():
        slug = _asesor_slug(c.get('asesor'), portal_lib)
        meta = {
            'id': c.get('id'),
            'nombre': c.get('nombre') or '',
            'asesor': slug or (c.get('asesor') or ''),
            'asignado_at': c.get('asignado_at') or '',
            'en_ejercicio': en_ejercicio(asignado_at=c.get('asignado_at')),
        }
        for field in ('telefono', 'telefono_alt'):
            n = norm_phone(c.get(field))
            if n and len(n) >= 10:
                out.setdefault(n, []).append(meta)
    return out


def telefonos_ocultos():
    """Teléfonos de clientes asignados antes del 31 oct 2026 (o sin fecha)."""
    return {n for n, lst in clientes_por_telefono().items() if all(not x['en_ejercicio'] for x in lst)}


def telefonos_visibles_de(user, *, portal_lib=None, db_propios=None):
    """Teléfonos que este usuario puede ver en Mensajes (después del corte).

    Admin: None significa «todos excepto ocultos».
    Asesor/líder: solo los de sus clientes asignados (y propios) en ejercicio.
    """
    if user.get('role') == 'admin':
        return None
    slug = user.get('user')
    allowed = set()
    for n, lst in clientes_por_telefono().items():
        if any(x['en_ejercicio'] and x['asesor'] == slug for x in lst):
            allowed.add(n)
    if db_propios:
        for r in db_propios([slug]):
            n = norm_phone(r.get('telefono'))
            if n and len(n) >= 10:
                allowed.add(n)
    return allowed


def puede_ver(user, wa_id, *, visibles=None, ocultos=None):
    n = norm_phone(wa_id)
    if not n:
        return user.get('role') == 'admin'
    if ocultos is None:
        ocultos = telefonos_ocultos()
    if n in ocultos:
        return False
    if user.get('role') == 'admin':
        return True
    if visibles is None:
        visibles = telefonos_visibles_de(user)
    return n in (visibles or ())


def canal_de(row):
    """Canal de la fila. No adivina por subcadena (evita marcar ghl_import como Instagram)."""
    data = row.get('data') or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    wa = str(row.get('wa_id') or '')
    if wa.startswith('ig:'):
        return 'instagram'
    if wa.startswith('fb:'):
        return 'facebook'
    if wa.startswith('email:'):
        return 'email'
    if wa.startswith('sms:'):
        return 'sms'
    ch = (data.get('channel') or data.get('canal') or '').strip().lower()
    if ch in ('whatsapp', 'instagram', 'facebook', 'email', 'sms'):
        return ch
    src = (row.get('source') or '').strip().lower()
    if src in ('whatsapp', 'instagram', 'facebook', 'email', 'sms'):
        return src
    return 'whatsapp'


def canal_etiqueta(canal):
    return {'whatsapp': 'WhatsApp', 'instagram': 'IG', 'facebook': 'FB', 'email': 'Email', 'sms': 'SMS'}.get(canal, 'WhatsApp')


def iniciales(nombre, telefono=''):
    parts = [p for p in re.split(r'\s+', (nombre or '').strip()) if p and not p.startswith('+')]
    if len(parts) >= 2:
        return (parts[0][:1] + parts[1][:1]).upper()
    if parts:
        w = parts[0]
        return w[:2].upper() if len(w) >= 2 else (w[:1] + (telefono[-1:] if telefono else w[:1])).upper()
    d = ''.join(c for c in (telefono or '') if c.isdigit())
    return (d[-2:] if len(d) >= 2 else 'C').upper()


def color_avatar(clave):
    s = clave or ''
    h = sum(ord(c) * (i + 1) for i, c in enumerate(s))
    return AVATAR_COLORS[h % len(AVATAR_COLORS)]


def hora_lista(iso, parse_caracas, now=None):
    """Hoy: HH:MM (Caracas). Si es más antiguo: fecha corta."""
    dt = parse_caracas(iso)
    if not dt:
        return ''
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('America/Caracas'))
    if dt.date() == now.date():
        return dt.strftime('%H:%M')
    meses = ('ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic')
    if dt.year == now.year:
        return f'{dt.day} {meses[dt.month - 1]}'
    return f'{dt.day} {meses[dt.month - 1]} {dt.year}'
