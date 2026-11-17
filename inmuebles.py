"""Sección Inmuebles con el estilo de Wasi (lista móvil, filtros y ficha). Solo lectura.

Reglas (José, 16-nov-2026):
- Solo datos reales del inventario (propias + aliados). La «red» no entra, igual que antes.
- Sin enlaces de Rex/Wasi/ML ni de inmuebles ajenos. El encargado (captador) solo lo ven Presidencia y Mercadeo,
  que ya lo ven en Mercadeo; para el resto de roles no cambia nada.
- Campos que no tenemos (coordenadas, área de terreno en la mayoría, etiquetas, inactivos) no se inventan:
  el filtro aparece desactivado con el motivo.
"""
from __future__ import annotations

import html
import re
import unicodedata

import portales

ESTADO_PUB = {'Active': 'Activo para difusión', 'Outstanding': 'Destacado', 'Inactive': 'Inactivo'}
ESTADO_FISICO = {'Used': 'Usado', 'New': 'Nuevo', 'Under construction': 'En construcción', 'Plan': 'Sobre planos'}
LISTAS = {'empresa': 'Mi empresa', 'propias': 'Propias de For Rent', 'aliados': 'Aliados', 'mias': 'Mis captaciones'}
ORDENES = {'reciente': 'Más reciente', 'antiguo': 'Más antiguo', 'mayor': 'Mayor precio', 'menor': 'Menor precio'}
_F_COMPAT = {'propias': 'propias', 'aliados': 'aliados', 'todos': 'empresa'}


def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')).lower() if unicodedata.category(c) != 'Mn').strip()


def _num(v):
    try:
        n = float(str(v).replace(',', '.'))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def precio_num(precio):
    d = re.sub(r'\D', '', str(precio or ''))
    return int(d) if d else None


def texto_plano(h, limite=4000):
    """Descripción de Wasi (HTML) a texto plano seguro."""
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1\s*>', '', str(h or ''))
    s = re.sub(r'(?i)<\s*br\s*/?>|</p>|</li>', '\n', s)
    s = html.unescape(re.sub(r'<[^>]+>', '', s))
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\n\s*\n+', '\n\n', s).strip()
    return s[:limite]


def ficha(it, ficha_publica, *, encargado=''):
    """Une la ficha pública (título, precio, fotos…) con los campos extra opcionales del inventario."""
    hab, ban, gar = _num(it.get('habitaciones')), _num(it.get('banos')), _num(it.get('garajes'))
    m2 = _num(it.get('m2'))
    estado = ESTADO_PUB.get(it.get('estado_pub') or '', '') or 'Disponible'
    zona, ciudad, region = it.get('zona') or '', it.get('ciudad') or '', it.get('region') or ''
    lugar = ', '.join(x for x in (zona, ciudad, region, it.get('pais') or '') if x)
    op = ficha_publica.get('operacion') or ''
    return {
        **ficha_publica,
        'key': portales.key_de(it), 'origen': it.get('origen') or '',
        'tipo_negocio': f"{ficha_publica.get('tipo') or 'Inmueble'} para {op}".strip() if op else (ficha_publica.get('tipo') or ''),
        'lugar': lugar, 'ciudad': ciudad, 'region': region,
        'estado': estado, 'destacado': it.get('estado_pub') == 'Outstanding',
        'habitaciones': int(hab) if hab else None, 'banos': int(ban) if ban else None,
        'garajes': int(gar) if gar else None, 'm2': m2, 'area_total': _num(it.get('area_total')),
        'estado_fisico': ESTADO_FISICO.get(it.get('estado_fisico') or '', it.get('estado_fisico') or ''),
        'descripcion': texto_plano(it.get('descripcion')),
        'actualizado_en': it.get('actualizado_en') or '',
        'precio_n': precio_num(ficha_publica.get('precio')),
        'encargado': encargado,
        'area_privada': _num(it.get('area_privada')), 'anio': str(it.get('anio') or '').strip(),
        'piso': str(it.get('piso') or '').strip(), 'tiene_video': bool(it.get('tiene_video')),
        'caracteristicas_int': list(it.get('caracteristicas_int') or []),
        'caracteristicas_ext': list(it.get('caracteristicas_ext') or []),
        'lat': _num(it.get('lat')), 'lng': _num(it.get('lng')),
    }


def lista_desde_args(args):
    lista = args.get('lista') or _F_COMPAT.get(args.get('f') or '', '') or 'empresa'
    return lista if lista in LISTAS else 'empresa'


def filtrar(fichas, args, *, mias=None):
    """Filtros tipo Wasi sobre las fichas (en servidor; la URL se puede compartir)."""
    lista = lista_desde_args(args)
    tipos = set(args.getlist('tipo')) if hasattr(args, 'getlist') else set()
    ops = set(args.getlist('op')) if hasattr(args, 'getlist') else set()
    zonas = set(args.getlist('zona')) if hasattr(args, 'getlist') else set()
    estados = set(args.getlist('estado')) if hasattr(args, 'getlist') else set()
    pmin, pmax = precio_num(args.get('pmin')), precio_num(args.get('pmax'))
    amin, amax = _num(args.get('amin')), _num(args.get('amax'))
    hmin = _num(args.get('hab'))
    fotos = args.get('fotos') == '1'
    texto = _norm(args.get('q'))
    out = []
    for f in fichas:
        if lista == 'propias' and f['origen'] != 'propio' or lista == 'aliados' and f['origen'] != 'alianza':
            continue
        if lista == 'mias' and f['key'] not in (mias or set()):
            continue
        if tipos and f.get('tipo') not in tipos or ops and f.get('operacion') not in ops:
            continue
        if zonas and f.get('zona') not in zonas or estados and f.get('estado') not in estados:
            continue
        p = f.get('precio_n')
        if (pmin and (p is None or p < pmin)) or (pmax and (p is None or p > pmax)):
            continue
        a = f.get('m2')
        if (amin and (a is None or a < amin)) or (amax and (a is None or a > amax)):
            continue
        if hmin and (f.get('habitaciones') or 0) < hmin:
            continue
        if fotos and not f.get('fotos'):
            continue
        if texto and texto not in _norm(' '.join(str(f.get(k) or '') for k in ('titulo', 'codigo', 'zona', 'tipo', 'lugar'))):
            continue
        out.append(f)
    orden = args.get('orden') if args.get('orden') in ORDENES else 'reciente'
    if orden in ('mayor', 'menor'):
        out.sort(key=lambda f: (f.get('precio_n') is None, (-(f.get('precio_n') or 0)) if orden == 'mayor' else (f.get('precio_n') or 0)))
    else:
        # por día de actualización; dentro del mismo día, primero los que tienen fotos (como Wasi)
        out.sort(key=lambda f: ((f.get('actualizado_en') or '')[:10], bool(f.get('fotos'))), reverse=(orden == 'reciente'))
    return out, lista, orden


def opciones(fichas):
    """Valores reales disponibles para cada filtro (nada inventado)."""
    def uniq(k):
        return sorted({f.get(k) for f in fichas if f.get(k)}, key=_norm)
    return {'tipo': uniq('tipo'), 'op': uniq('operacion'), 'zona': uniq('zona'), 'estado': uniq('estado')}


def compartir_texto(f):
    """Texto para compartir SIN enlaces (regla: nada de links de otras agencias)."""
    partes = [f.get('titulo') or '', f.get('tipo_negocio') or '', f.get('lugar') or f.get('zona') or '',
              f"Precio: {f['precio']}" if f.get('precio') else '', f"m²: {f['m2']:g}" if f.get('m2') else '',
              f"Código: {f['codigo']}" if f.get('codigo') else '', 'For Rent Inmobiliaria']
    return '\n'.join(p for p in partes if p)
