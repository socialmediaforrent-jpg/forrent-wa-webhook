"""Estructura la nota de asignación de un cliente, sin inventar datos.

Parsea el texto que ya llega en inmueble/propiedad_raw (el bloque largo de
BUSCA) y lo arma en secciones. Lo que no se puede clasificar queda en
«Otras notas». No completa huecos ni inventa códigos, precios ni contactos.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_CODE = re.compile(r'\b(Rex|Wasi|MLV)[\s:\-]*((?=[A-Z0-9-]*\d)[A-Z]{0,4}-?[A-Z0-9]{3,})\b', re.I)
_LINK = re.compile(r'https?://[^\s<>]+')
_EMAIL = re.compile(r'[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}', re.I)
_PHONE = re.compile(r'(?:\+?\s*58[\s\-]*)?(?:0?4\d{2})[\s\-]?\d{3}[\s\-]?\d{4}')
_PROP_START = re.compile(
    r'(?=(?:NO ofrecer\s+|Puede retomar\s+|Mónica también le ofreció\s+))',
    re.I,
)
_OJO = re.compile(r'\bOJO\s*:\s*', re.I)
_REEL = re.compile(
    r'reel\s+(\d+)\s*(?:IG\s+)?(https?://[^\s.,;)]+|Df[\w]+|[A-Za-z0-9_-]{8,})?',
    re.I,
)
_RUEDA = re.compile(r'por rueda\s+([^.(]+?)(?:\s+a\s+[A-ZÁÉÍÓÚÑ]|$|\()', re.I)
_NO_RUEDA = re.compile(r'NO por rueda', re.I)
_PRESUP = re.compile(
    r'(?:Presupuesto\s+)?(US\$\s*[\d.]+\s*(?:[–\-]|a(?:\s*máximo)?)\s*US\$\s*[\d.]+|US\$\s*[\d.]+)(?:\s*al mes)?',
    re.I,
)
_HABS = re.compile(
    r'(\d+\s*o\s*\d+|\d+)\s*(?:habitaciones|hab\.?)\b',
    re.I,
)
_ZONAS_LABEL = re.compile(r'Zonas?\s*:\s*([^.]+)', re.I)
_PLAZO = re.compile(r'Los\s+(\d+\s+meses)\s+los cubre[^.]*', re.I)
_OPER_TXT = re.compile(
    r'(?:Búsqueda|Visita|Visita de|Busca)\s+de\s+(ALQUILER|COMPRA|VENTA)\b|'
    r'Operación\s*:\s*(alquiler|venta|compra)\b|'
    r'\b(COMPRA DIRECTA)\b',
    re.I,
)
_CLI_DIR = re.compile(r'cliente direct[oa]', re.I)


_FRASES = re.compile(
    r'Frases\s*:\s*(.+?)(?=\s+(?:En GHL\b|WhatsApp\s+\+|Operación\s*:|No se le dio)|$)',
    re.I | re.S,
)
_WA_CLIENTE = re.compile(r'WhatsApp\s+(\+?[\d][\d \-]{7,}\d)', re.I)


def _saca_frases(texto):
    """Devuelve (texto sin el bloque Frases, lista de frases citadas)."""
    m = _FRASES.search(texto or '')
    if not m:
        return texto, []
    bloque = m.group(1).strip().rstrip('.')
    citas = re.findall(r'«([^»]+)»', bloque)
    if not citas:
        citas = [bloque]
    resto = (texto[:m.start()] + ' ' + texto[m.end():]).strip()
    return resto, [_limpia(c) for c in citas if _limpia(c)]


def _saca_whatsapp(texto):
    m = _WA_CLIENTE.search(texto or '')
    if not m:
        return texto, ''
    tel = _limpia(m.group(1))
    resto = (texto[:m.start()] + texto[m.end():]).strip()
    resto = re.sub(r'\s+\.', '.', resto)
    resto = re.sub(r'^\.\s*', '', resto)
    return resto, tel


def _limpia(s):
    s = (s or '').replace('\xa0', ' ')
    s = re.sub(r'[ \t]+', ' ', s).strip()
    s = re.sub(r'\s+([.,;:])', r'\1', s)
    return s


def _frases(texto):
    """Parte en oraciones toscas, conservando el texto."""
    t = _limpia(texto)
    if not t:
        return []
    parts = re.split(r'(?<! [A-Z]\.)(?<=\.)\s+(?=[A-ZÁÉÍÓÚÑ¿¡«])', t)
    return [p.strip() for p in parts if p.strip()]


def _capital(s):
    s = _limpia(s)
    if not s:
        return s
    return s[0].upper() + s[1:] if s[0].islower() else s


def _badge_de(chunk):
    badges = []
    low = chunk.lower()
    if re.search(r'\bno ofrecer\b', low) or re.search(r'\bno está activo\b', low):
        badges.append('NO ofrecer')
    if re.search(r'alianza no confirmada', low):
        badges.append('Alianza no confirmada')
    if re.search(r'\bdisponible\b', low) and 'NO ofrecer' not in badges:
        badges.append('Disponible')
    return badges


def _codes(chunk):
    found = []
    for m in _CODE.finditer(chunk or ''):
        kind = m.group(1).title()
        if kind.lower() == 'mlv':
            kind = 'MLV'
        found.append({'fuente': kind.lower(), 'label': f'{kind} {m.group(2)}', 'codigo': m.group(2)})
    # unique by label, keep order
    out, seen = [], set()
    for c in found:
        if c['label'] not in seen:
            seen.add(c['label'])
            out.append(c)
    return out


def _links(chunk):
    out = []
    for m in _LINK.finditer(chunk or ''):
        url = m.group(0).rstrip(').,;')
        host = (urlparse(url).hostname or '').lower()
        if 'rexup' in host or 'rex' in host:
            label = 'Ver en Rex'
        elif 'wasi' in host:
            label = 'Ver en Wasi'
        elif 'mercadolibre' in host or 'mlv' in url.lower():
            label = 'Ver en Mercado Libre'
        else:
            label = 'Abrir enlace'
        out.append({'url': url, 'label': label})
    return out


def _captador(chunk):
    """Contacto del captador, marcado siempre como SOLO ASESOR si aparece."""
    c = chunk or ''
    m = re.search(
        r'(?:Datos en sistema|Captadora?)\s*\(\s*SOLO asesora?\s*\)\s*:\s*(.+?)(?:\.\s|Link:|$)',
        c, re.I,
    )
    if not m:
        m = re.search(
            r'constan\s+(.+?)(?:\.\s|Link:|Alianza|$)',
            c, re.I,
        )
    nombre = oficina = telefono = email = ''
    if m:
        bits = [b.strip(' .;') for b in m.group(1).split(',') if b.strip()]
        if bits:
            nombre = re.sub(r'\s+', ' ', bits[0]).strip()
        emails = _EMAIL.findall(m.group(1))
        if emails:
            email = emails[0].rstrip('.')
        phones = _PHONE.findall(m.group(1))
        if phones:
            telefono = re.sub(r'\s+', ' ', phones[0]).strip()
        # middle token often the office
        mid = [b for b in bits[1:] if b and '@' not in b and not _PHONE.search(b)]
        if mid:
            oficina = mid[0]
    if not nombre:
        m2 = re.search(r'Captadora? en ficha\s+\w+\s+hoy\s*:\s*([A-ZÁÉÍÓÚÑ][^.]{1,80})', c, re.I)
        if m2:
            nombre = _limpia(m2.group(1))
    if not (nombre or telefono or email):
        return None
    return {
        'nombre': nombre,
        'oficina': oficina,
        'telefono': telefono,
        'email': email,
        'solo_asesor': True,
    }


def _titulo_zona(chunk, codes):
    t = chunk or ''
    t = re.sub(r'^(?:NO ofrecer|Puede retomar|Mónica también le ofreció)\s+', '', t, flags=re.I)
    # cut at first code or parenthesis with measurements
    if codes:
        t = re.split(re.escape(codes[0]['label']) + r'|' + re.escape(codes[0]['codigo']), t, maxsplit=1, flags=re.I)[0]
    t = t.split(':')[0]
    t = re.sub(r'[,;]\s*$', '', t)
    t = _limpia(t)
    # "El Paraíso, 3 hab, 112 m², US$750" -> El Paraíso
    t = re.split(r',\s*(?:\d+\s*hab|\d+\s*m|US\$)', t, maxsplit=1)[0]
    t = _limpia(t.strip(' ,.;'))
    return t[:80]


def _medidas(chunk):
    hab = m2 = precio = banos = puesto = ''
    m = re.search(r'(\d+)\s*hab(?:itaciones)?\b', chunk or '', re.I)
    if m:
        hab = m.group(1)
    else:
        m = re.search(r'Habitaciones\s*:\s*(\d+)', chunk or '', re.I)
        if m:
            hab = m.group(1)
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*m²', chunk or '', re.I)
    if not m:
        m = re.search(r'Área\s+(\d+(?:[.,]\d+)?)\s*m', chunk or '', re.I)
    if m:
        m2 = m.group(1).replace(',', '.')
    m = re.search(r'US\$\s*\d(?:[\d.]*\d)?', chunk or '', re.I)
    if m:
        precio = re.sub(r'\s+', '', m.group(0))
    m = re.search(r'(\d+)\s*baños?', chunk or '', re.I)
    if m:
        banos = m.group(1)
    m = re.search(r'(\d+)\s*(?:puesto|estacionamiento)', chunk or '', re.I)
    if m:
        puesto = m.group(1)
    estado = ''
    if re.search(r'\bno está activo\b|\bno existe\b', chunk or '', re.I):
        estado = 'No está activo'
    elif re.search(r'\bdisponible\b', chunk or '', re.I):
        estado = 'Disponible'
    return {'hab': hab, 'm2': m2, 'precio': precio, 'banos': banos, 'puesto': puesto, 'estado': estado}


def _inmueble_de(chunk):
    chunk = _limpia(chunk)
    codes = _codes(chunk)
    med = _medidas(chunk)
    cap = _captador(chunk)
    links = _links(chunk)
    titulo = _titulo_zona(chunk, codes)
    return {
        'titulo': titulo,
        'codigos': codes,
        'zona': titulo,
        'hab': med['hab'],
        'm2': med['m2'],
        'precio': med['precio'],
        'banos': med['banos'],
        'puesto': med['puesto'],
        'estado': med['estado'],
        'badges': _badge_de(chunk),
        'captador': cap,
        'links': links,
        'texto': chunk,
    }


def _split_inmuebles(texto):
    t = _limpia(texto)
    if not t:
        return '', [], ''
    parts = _PROP_START.split(t)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return t, [], ''
    pre, rest = parts[0], parts[1:]
    # If the last chunk contains closing notes (Frases, WhatsApp, Operación…), split them off.
    tail_re = re.compile(
        r'(?=(?:Aún no dijo|Ana debe escribirle|Frases\s*:|En GHL\b|WhatsApp\s+\+|Operación\s*:|No se le dio))',
        re.I,
    )
    inms, tail = rest, ''
    if rest:
        last = rest[-1]
        bits = tail_re.split(last, maxsplit=1)
        if len(bits) == 2:
            inms = rest[:-1] + ([bits[0].strip()] if bits[0].strip() else [])
            tail = bits[1].strip()
    return pre, inms, tail


def _resumen(cliente, pre, raw):
    filas = []
    op = (cliente.get('operacion') or '').strip()
    if not op:
        m = _OPER_TXT.search(raw or pre or '')
        if m:
            op = next((g for g in m.groups() if g), '')
    if op:
        filas.append({'label': 'Operación', 'value': _capital(op.lower() if op.isupper() else op)})
    tipo = (cliente.get('tipo_lead') or '').strip()
    if tipo:
        tipo = tipo.replace('Busqueda', 'Búsqueda').replace('busqueda', 'búsqueda')
        filas.append({'label': 'Tipo de lead', 'value': tipo})
    rueda = ''
    if _NO_RUEDA.search(pre or raw or ''):
        rueda = 'No va por rueda'
    else:
        m = _RUEDA.search(pre or raw or '')
        if m:
            rueda = 'Rueda ' + _limpia(m.group(1))
        elif re.search(r'por rueda', pre or raw or '', re.I):
            rueda = 'Por rueda'
    if rueda:
        filas.append({'label': 'Rueda', 'value': rueda})
    m = _REEL.search(pre or raw or '')
    if m:
        n, tag = m.group(1), (m.group(2) or '').rstrip(').,')
        val = f'Reel {n}'
        if tag:
            val += f' IG {tag}'
        filas.append({'label': 'Origen', 'value': val})
    elif cliente.get('fuente'):
        filas.append({'label': 'Origen', 'value': str(cliente.get('fuente'))})
    if _CLI_DIR.search(pre or raw or ''):
        filas.append({'label': 'Cliente directo', 'value': 'Sí'})
    return filas


def _busca(pre, raw):
    filas = []
    zona = ''
    m = _ZONAS_LABEL.search(pre or raw or '')
    if m:
        zona = _limpia(m.group(1))
    else:
        m = re.search(
            r'(?:en|de los de\s+\d+\s+habitaciones en)\s+'
            r'([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚáéíóúñ. ]+(?:\s*y\s*[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚáéíóúñ. ]+)*)'
            r'(?:;|\.|,)?\s*(?:([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚáéíóúñ. ]+)\s*«?también puede ser»?)?',
            pre or '',
        )
        if m:
            zona = _limpia(m.group(1))
            if m.group(2):
                zona = zona + '; ' + _limpia(m.group(2)) + ' (también puede ser)'
    if zona:
        filas.append({'label': 'Zonas', 'value': zona})
    m = _HABS.search(pre or raw or '')
    if m:
        filas.append({'label': 'Habitaciones', 'value': _limpia(m.group(1))})
    m = _PRESUP.search(pre or raw or '')
    if m:
        val = _limpia(m.group(1))
        if re.search(r'al mes', m.group(0) if m else '', re.I) and 'mes' not in val.lower():
            val += ' al mes'
        filas.append({'label': 'Presupuesto', 'value': val})
    m = _PLAZO.search(pre or raw or '')
    if m:
        filas.append({'label': 'Plazo', 'value': _limpia(m.group(0).rstrip('.'))})
    conds = []
    blob = pre or raw or ''
    if re.search(r'negociable', blob, re.I):
        conds.append('Preguntó si el precio es negociable o es definitivo')
    if re.search(r'\bamoblado\b', blob, re.I):
        conds.append('Amoblado')
    if re.search(r'con puesto', blob, re.I):
        conds.append('Con puesto de estacionamiento')
    if conds:
        filas.append({'label': 'Condiciones', 'value': '; '.join(conds)})
    # frases de pedido que no hayan quedado cubiertas
    for fr in _frases(pre):
        if re.match(r'^(Pidió|Busca|Preguntó|Quiere)', fr, re.I):
            already = any(fr[:40].lower() in (f['value'] or '').lower() for f in filas)
            if not already:
                filas.append({'label': 'Detalle', 'value': _capital(fr.rstrip('.;'))})
    return filas


def _ojos_y_notas(pre, tail, inmueble_chunks, raw, usados):
    ojos, otras = [], []
    # OJO in the whole raw: extract the OJO sentence(s)
    for fr in _frases(raw):
        if _OJO.search(fr) or fr.upper().startswith('OJO'):
            txt = _OJO.sub('', fr).strip()
            ojos.append(_capital(txt.rstrip('.;')))
            usados.append(fr)

    candidatos = _frases(pre) + _frases(tail)
    skip_pref = (
        r'^(Búsqueda de |Visita de |Visita Punta |Cliente direct|'
        r'Pidió |Busca |Presupuesto |Zonas?:|Operación:|Los \d+ meses|'
        r'pregunta?ó si el precio)'
    )
    for fr in candidatos:
        if any(_parece(fr, u) for u in usados):
            continue
        if _OJO.search(fr) or fr.upper().startswith('OJO'):
            continue
        if re.match(skip_pref, fr, re.I):
            continue
        if _CLI_DIR.fullmatch(fr.rstrip('.')):
            continue
        if _OPER_TXT.search(fr) and len(fr) < 40:
            continue
        otras.append(_capital(fr.rstrip('.;')))
    return ojos, otras


def _parece(a, b):
    """True si dos textos son el mismo dato. Valores cortos («3») no cuentan como contenidos."""
    a, b = _limpia(a).lower().rstrip('.;'), _limpia(b).lower().rstrip('.;')
    if not a or not b:
        return False
    if a == b:
        return True
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    if len(corto) >= 24 and corto in largo:
        return True
    return len(a) >= 50 and len(b) >= 50 and a[:50] == b[:50]


def texto_fuente(cliente):
    """El bloque largo de la asignación, si existe, sin inventar otro."""
    raw = (cliente.get('propiedad_raw') or '').strip()
    inm = (cliente.get('inmueble') or '').strip()
    if raw and (not inm or raw.startswith(inm) or inm in raw or len(raw) >= len(inm)):
        return raw
    if inm:
        return inm
    return raw


def resumen_corto(cliente):
    """Una línea para la ficha (no el párrafo largo)."""
    raw = texto_fuente(cliente)
    corto = (cliente.get('inmueble_corto') or '').strip()
    if corto and corto != raw and len(corto) < 180:
        return corto
    if raw and len(raw) > 180:
        # primera oración si es corta
        p1 = _frases(raw)[:1]
        if p1 and len(p1[0]) <= 180:
            return p1[0]
        return raw[:157].rstrip() + '…'
    return raw or (cliente.get('operacion') or '')


def parse_asignacion(cliente):
    """Devuelve secciones listas para la plantilla."""
    raw = texto_fuente(cliente)
    pre, chunks, tail = _split_inmuebles(raw)
    # Fallback: un solo inmueble con código en el preámbulo (visita directa, etc.).
    if not chunks and _CODE.search(pre or raw or ''):
        # separamos una primera frase de resumen si la hay
        frs = _frases(pre or raw)
        if frs and not _CODE.search(frs[0]) and len(frs) > 1:
            pre = frs[0]
            chunks = [' '.join(frs[1:])]
            tail = ''
            # re-split tail from last
            tail_re = re.compile(
                r'(?=(?:Aún no dijo|Ana debe escribirle|Frases\s*:|En GHL\b|WhatsApp\s+\+|Operación\s*:|No se le dio))',
                re.I,
            )
            bits = tail_re.split(chunks[0], maxsplit=1)
            if len(bits) == 2:
                chunks, tail = [bits[0].strip()], bits[1].strip()
        else:
            chunks = [pre or raw]
            pre = ''

    tail, frases = _saca_frases(tail)
    tail, wa = _saca_whatsapp(tail)
    inmuebles = [_inmueble_de(ch) for ch in chunks]
    usados = list(chunks)
    busca = _busca(pre, raw)
    ojos, otras = _ojos_y_notas(pre, tail, chunks, raw, usados)
    # No repetir en «Otras notas» lo que ya quedó en «Qué busca».
    cubiertos = [f['value'] for f in busca]
    otras = [o for o in otras if not any(_parece(o, c) for c in cubiertos)
             and not re.fullmatch(r'Operación\s*:\s*\w+', o, re.I)]
    info = {
        'resumen': _resumen(cliente, pre, raw),
        'busca': busca,
        'inmuebles': inmuebles,
        'ojos': ojos,
        'frases': frases,
        'whatsapp_texto': wa,
        'otras_notas': otras,
        'tiene_contenido': bool(raw or cliente.get('operacion') or cliente.get('tipo_lead')),
        'texto_original': raw,
    }
    return info
