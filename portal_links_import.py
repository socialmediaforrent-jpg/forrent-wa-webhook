#!/usr/bin/env python3
"""Carga enlaces REALES de portales a crm_portal_links desde fuentes verificadas de la caja.

Fuentes (no inventa nada):
- Mercado Libre: MLV del inventario diario (reports/inventario_para_monica_*.csv) que además aparecen
  en el panel de publicaciones activas de ML del día (reports/ml_activas_panel_*.txt). La URL es el
  permalink capturado de ML si lo tenemos (--ml-urls) o el permalink corto oficial de ML.
- Wasi: ficha pública del sitio Wasi de For Rent (link_publico) o, si falta, el ``link`` que devuelve
  la API de Wasi cuando la ficha está activa (--wasi-env, solo lectura).
- Rex: ficha de Rex del inventario diario (los Rex inactivos ya salen de ese inventario). En inmuebles de
  alianza la ficha Rex es la del aliado (con sus datos), así que se guarda como ``ajena`` y no se muestra.
Los otros 7 portales no se tocan: quedan vacíos hasta que se publique y se cargue el enlace.
Cada URL se verifica por HTTP antes de guardarse (ok / protegido por anti-robots / caido).

Uso:  DATABASE_URL=... python3 portal_links_import.py [--dry-run] [--sqlite ruta.db]
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import portales

ROOT = '/workspace/forrent'
FUENTE_ML = 'import: panel ML activas {f} + inventario diario'
FUENTE_WASI = 'import: ficha Wasi del inventario diario {f}'
FUENTE_WASI_API = 'import: API Wasi (ficha activa) {f}'
FUENTE_REX = 'import: ficha Rex del inventario diario {f} (Rex activo)'


def latest(pat):
    xs = sorted(glob.glob(os.path.join(ROOT, 'reports', pat)))
    return xs[-1] if xs else None


def make_q(dsn=None, sqlite_path=None):
    if dsn:
        import psycopg
        conn = psycopg.connect(dsn, autocommit=True)

        def q(sql, args=(), fetch=False):
            cur = conn.execute(sql, args)
            if not fetch:
                return None
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        return q, True
    conn = sqlite3.connect(sqlite_path, check_same_thread=False)

    def q(sql, args=(), fetch=False):
        cur = conn.execute(sql.replace('%s', '?'), args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        if rows is None:
            return None
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    return q, False


def _env(p):
    e = {}
    for line in open(p):
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            e[k.strip()] = v.strip().strip('"')
    return e


def wasi_link(pid, env):
    try:
        url = f"https://api.wasi.co/v1/property/get/{pid}?id_company={env['WASI_ID_COMPANY']}&wasi_token={env['WASI_TOKEN']}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'forrent-portal'}), timeout=30) as f:
            d = json.loads(f.read())
        if str(d.get('id_status_on_page')) == '1' and str(d.get('link') or '').startswith('https://'):
            return d['link']
    except Exception:
        return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default=latest('inventario_para_monica_2*.csv'))
    ap.add_argument('--ml-panel', default=latest('ml_activas_panel_*.txt'))
    ap.add_argument('--ml-urls', help='archivo con permalinks de ML ya capturados (uno por línea)')
    ap.add_argument('--wasi-env', default=os.path.join(ROOT, 'wasi_api.env'))
    ap.add_argument('--sqlite')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    fecha = re.search(r'(\d{8})', os.path.basename(a.csv)).group(1)
    fpanel = re.search(r'(\d{8})', os.path.basename(a.ml_panel)).group(1)
    activos = {f'MLV-{n}' for n in re.findall(r'\d{6,}', open(a.ml_panel).read())}
    permalinks = {}
    if a.ml_urls and os.path.exists(a.ml_urls):
        for line in open(a.ml_urls):
            m = re.match(r'(https://[a-z]+\.mercadolibre\.com\.ve/(MLV-\d+)[-\w]*)', line.strip())
            # Solo permalinks completos (terminan en -_JM); los recortados se reemplazan por el corto oficial.
            if m and m.group(1).endswith('-_JM'):
                permalinks.setdefault(m.group(2), m.group(1))
    wenv = _env(a.wasi_env) if a.wasi_env and os.path.exists(a.wasi_env) else None

    plan = []  # (it, portal, url, fuente, nota)
    for r in csv.DictReader(open(a.csv, encoding='utf-8')):
        o = (r.get('origen') or '').strip()
        if o not in ('propio', 'alianza'):
            continue
        it = {'codigo': (r.get('id_wasi') or r.get('id_rex') or '').strip() or '—', 'origen': o,
              'operacion': r.get('operacion'), 'tipo': r.get('tipo'), 'zona': (r.get('zona') or '').strip(),
              'precio': r.get('precio_usd')}
        mlvs = [m.strip().upper() for m in (r.get('mlv') or '').split('|') if m.strip()]
        act = [m for m in mlvs if m in activos]
        if act:
            url = permalinks.get(act[0]) or f'https://articulo.mercadolibre.com.ve/{act[0]}-_JM'
            nota = ('Otras publicaciones activas: ' + ', '.join(act[1:])) if len(act) > 1 else ''
            plan.append((it, 'mercadolibre', url, FUENTE_ML.format(f=fpanel), nota))
        link = (r.get('link_publico') or '').strip()
        idw, idr = (r.get('id_wasi') or '').strip(), (r.get('id_rex') or '').strip()
        if idw and 'grupoinmobiliarioforrent.com' in link and link.rstrip('/').endswith('/' + idw):
            plan.append((it, 'wasi', link, FUENTE_WASI.format(f=fecha), ''))
        elif idw and wenv:
            wl = wasi_link(idw, wenv)
            if wl:
                plan.append((it, 'wasi', wl, FUENTE_WASI_API.format(f=fecha), ''))
        if idr and 'rexup.net' in link and f'cod_inmueble={idr}' in link:
            aliado = (r.get('alianza_de') or '').strip()
            if o == 'alianza':
                # La ficha Rex de un inmueble de alianza es la del aliado (sus datos de contacto): no es nuestra.
                plan.append((it, 'rex', link, FUENTE_REX.format(f=fecha),
                             f'AJENA: ficha Rex de {aliado or "otro captador"}, no es publicación nuestra'))
            else:
                plan.append((it, 'rex', link, FUENTE_REX.format(f=fecha), ''))

    def check(x):
        it, portal, url, fuente, nota = x
        if nota.startswith('AJENA:'):
            return x, ('ajena', None, '')
        return x, portales.verificar(portal, url)
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(check, plan))

    resumen = {}
    for (it, portal, url, fuente, nota), (estado, st, det) in res:
        resumen.setdefault(portal, {}).setdefault(estado, 0)
        resumen[portal][estado] += 1
    print(json.dumps({'csv': os.path.basename(a.csv), 'ml_panel': os.path.basename(a.ml_panel),
                      'enlaces': len(plan), 'por_portal': resumen}, ensure_ascii=False))
    if a.dry_run:
        for (it, portal, url, fuente, nota), (estado, st, det) in res:
            print(portal, estado, st, it['codigo'], url, det)
        return

    q, is_pg = make_q(os.environ.get('DATABASE_URL'), a.sqlite)
    portales.init(q, is_pg)
    vistos = set()
    for (it, portal, url, fuente, nota), (estado, st, det) in res:
        key = portales.guardar(q, it=it, portal=portal, url=url, estado=estado, http_status=st,
                               fuente=fuente, nota=('; '.join(x for x in (nota, det) if x) or None), por='import')
        vistos.add((key, portal))
    # Lo importado antes que ya no aparece en las fuentes de hoy se marca como caído (no se borra).
    for row in q("SELECT id, inmueble_key, portal FROM crm_portal_links WHERE fuente LIKE 'import:%%'", fetch=True) or []:
        if (row['inmueble_key'], row['portal']) not in vistos:
            q("UPDATE crm_portal_links SET estado='caido', nota=%s, verificado_en=%s WHERE id=%s",
              ('Ya no aparece en las fuentes del día', portales.now_iso(), row['id']))
    n = q('SELECT portal, estado, COUNT(*) AS n FROM crm_portal_links GROUP BY portal, estado ORDER BY portal', fetch=True)
    print(json.dumps(n, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
