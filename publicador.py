"""Publicador del planificador propio — MODO SIMULACIÓN (solo registro).

Lo corre el Cron Job de Render cada 5 minutos: `python publicador.py`.
- Solo mira publicaciones del portal (origen='portal'); NUNCA las importadas de GHL (origen='ghl_import'),
  que GHL sigue publicando.
- Solo cuentas con interruptor encendido (social_accounts.publicar_auto=1). Hoy todas están apagadas.
- No contiene código que publique en ninguna red: solo escribe en el log qué publicaría.
  Publicar de verdad requiere otro cambio y el OK de José, cuenta por cuenta.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

MODO = 'simulacion'
log = logging.getLogger('publicador')


def pendientes(q, ahora_utc=None):
    """[(post, [cuentas encendidas])] vencidos y elegibles. No modifica nada."""
    ahora = (ahora_utc or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    encendidas = {r['clave'] for r in (q('SELECT clave FROM social_accounts WHERE activa=1 AND publicar_auto=1', fetch=True) or [])}
    rows = q("""SELECT id, origen, estado, programado_utc, cuentas FROM social_posts
                WHERE deleted_at IS NULL AND origen='portal' AND estado='Programado'
                  AND programado_utc IS NOT NULL AND programado_utc <> '' AND programado_utc <= %s
                ORDER BY programado_utc, id""", (ahora,), fetch=True) or []
    out, apagadas = [], 0
    for r in rows:
        assert r['origen'] == 'portal'  # defensa extra: jamás lo importado de GHL
        cs = [c for c in json.loads(r['cuentas'] or '[]') if c in encendidas]
        if cs:
            out.append((r, cs))
        else:
            apagadas += 1
    return out, apagadas, len(rows)


def correr(q):
    elegibles, apagadas, vencidas = pendientes(q)
    for r, cs in elegibles:
        log.info('[SIMULACIÓN] publicaría post %s (%s) en %s — no se envía nada', r['id'], r['programado_utc'], ', '.join(cs))
    log.info('[SIMULACIÓN] vencidas del portal: %s · con cuentas encendidas: %s · todas sus cuentas apagadas: %s',
             vencidas, len(elegibles), apagadas)
    return {'modo': MODO, 'vencidas': vencidas, 'elegibles': len(elegibles), 'apagadas': apagadas}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s %(levelname)s %(message)s')
    import app  # usa la misma conexión y crea las tablas si faltan (aditivas)
    print(json.dumps(correr(app.q)))
