"""Importación de enlaces de portales desde la lista de Inventario («código | portal | url»).

Reglas (José, 16-nov-2026):
- Idempotente: la misma lista dos veces no cambia nada (misma URL = «sin_cambio»).
- Nunca borra enlaces. Solo agrega o REEMPLAZA la URL del mismo inmueble y portal; cada reemplazo queda
  en crm_portal_links_log con la URL anterior (url_antes), que es el respaldo fila a fila.
- Un enlace nuevo que no responde («caido») no pisa a uno que ya existe.
- Solo dominios del portal (portales.url_valida). Rex de inmuebles en alianza = ficha del aliado → «ajena»
  (no se muestra; regla: links de otras agencias nunca a clientes).
- Solo inmuebles del inventario de la inmobiliaria (propio + alianza). Lo que no cuadra se reporta, no se inventa.
"""
from __future__ import annotations

import re

import portales

ALIAS = {
    'ml': 'mercadolibre', 'mercado libre': 'mercadolibre', 'mercadolibre': 'mercadolibre',
    'rex': 'rex', 'rexup': 'rex', 'wasi': 'wasi', 'housy': 'housy', 'thehousy': 'housy',
    'zonaven': 'zonaven', 'encuadras': 'encuadras', 'anuto': 'anuto', 'agentiz': 'agentiz',
    'propiedash': 'propiedash', 'bienesonline': 'bienesonline', 'bienes online': 'bienesonline',
}
MAX_FILAS = 500
POR = 'import:inventario'


def parse(texto):
    """Devuelve (filas, errores). Ignora líneas vacías, comentarios y la cabecera «Fuente: …»."""
    filas, errores = [], []
    for n, line in enumerate((texto or '').splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#') or s.lower().startswith('fuente') or '|' not in s:
            continue
        partes = [x.strip() for x in s.split('|')]
        if len(partes) < 3:
            errores.append({'linea': n, 'error': 'faltan columnas (código | portal | url)'})
            continue
        codigo, portal, url = partes[0], ALIAS.get(partes[1].lower(), partes[1].lower()), partes[2]
        if portal not in portales.PORTAL_KEYS:
            errores.append({'linea': n, 'codigo': codigo, 'error': f'portal desconocido: {partes[1]}'})
            continue
        filas.append({'linea': n, 'codigo': codigo, 'portal': portal, 'url': url})
        if len(filas) > MAX_FILAS:
            errores.append({'linea': n, 'error': f'más de {MAX_FILAS} filas; se corta'})
            break
    return filas, errores


def _item_por_codigo(codigo, items, q):
    for it in items:
        if str(it.get('codigo') or '').strip() == codigo:
            return it
    m = re.fullmatch(r'MLV-?(\d{6,})', codigo.upper())
    if m:  # inmueble sin código Wasi/Rex: se reconoce por su enlace de ML ya guardado
        rows = q("SELECT inmueble_key FROM crm_portal_links WHERE portal='mercadolibre' AND url LIKE %s",
                 (f'%MLV-{m.group(1)}%',), fetch=True) or []
        keys = {r['inmueble_key'] for r in rows}
        if len(keys) == 1:
            k = keys.pop()
            return next((it for it in items if portales.key_de(it) == k), None)
    return None


def importar(q, items, texto, *, dry_run=True, verificar=True, fuente='inventario: lista de Inventario'):
    """items = inventario propio + alianza. Devuelve el resumen; con dry_run no escribe nada."""
    filas, errores = parse(texto)
    actual = portales.links_por_key(q)
    res = {'filas': len(filas), 'agregados': 0, 'reemplazados': 0, 'sin_cambio': 0, 'rechazados': 0,
           'no_cuadra': 0, 'dry_run': bool(dry_run), 'detalle': [], 'errores': errores}
    vistos = set()
    for f in filas:
        d = {'linea': f['linea'], 'codigo': f['codigo'], 'portal': f['portal']}
        url, e = portales.url_valida(f['portal'], f['url'])
        if e:
            res['rechazados'] += 1; res['detalle'].append({**d, 'accion': 'rechazado', 'motivo': e}); continue
        it = _item_por_codigo(f['codigo'], items, q)
        if not it:
            res['no_cuadra'] += 1; res['detalle'].append({**d, 'accion': 'no_cuadra', 'motivo': 'no está en el inventario'}); continue
        key = portales.key_de(it)
        if (key, f['portal']) in vistos:
            res['detalle'].append({**d, 'accion': 'duplicado_en_lista'}); continue
        vistos.add((key, f['portal']))
        prev = (actual.get(key) or {}).get(f['portal'])
        if prev and prev.get('url') == url:
            res['sin_cambio'] += 1; res['detalle'].append({**d, 'accion': 'sin_cambio'}); continue
        ajena = f['portal'] == 'rex' and (it.get('origen') or '') == 'alianza'
        if ajena:
            estado, st, det = 'ajena', None, 'AJENA: ficha Rex del aliado, no es publicación nuestra'
        elif verificar:
            estado, st, det = portales.verificar(f['portal'], url)
        else:
            estado, st, det = 'pendiente', None, ''
        if prev and estado == 'caido':
            res['rechazados'] += 1
            res['detalle'].append({**d, 'accion': 'rechazado', 'motivo': 'el enlace nuevo no responde; se conserva el actual'})
            continue
        accion = 'reemplazado' if prev else 'agregado'
        res['agregados' if not prev else 'reemplazados'] += 1
        res['detalle'].append({**d, 'accion': accion, 'estado': estado, 'url_antes': (prev or {}).get('url')})
        if not dry_run:
            portales.guardar(q, it=it, portal=f['portal'], url=url, estado=estado, http_status=st,
                             fuente=fuente, nota=det or None, por=POR)
    return res


def exportar(q):
    """Respaldo completo de crm_portal_links (para guardar en la caja antes de importar)."""
    rows = q('SELECT * FROM crm_portal_links ORDER BY id', fetch=True) or []
    return {'tabla': 'crm_portal_links', 'n': len(rows), 'rows': rows}
