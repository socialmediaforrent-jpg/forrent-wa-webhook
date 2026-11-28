"""Modelo Estado + Situación + Comentario del Sistema de Clientes.

Copia fiel de forrent-estatus (app.py, commit 0d3e9e1, el que está live):
ESTADOS, MOTIVOS, situaciones generales (Buscando, Esperando) y validar().
Si cambia allá, cambiar aquí igual. La app no inventa estados propios.
"""
from __future__ import annotations

import re

# estado -> lista de (situación, pide_fecha, fecha_obligatoria); 'motivo' = texto obligatorio
ESTADOS = {
    "Nuevo": {"sits": [("Contactado", 0, 0), ("No contactado", 0, 0)]},
    "En proceso": {"sits": [("Visita agendada", 1, 1), ("Visita realizada", 1, 1)]},
    "Negociación": {"sits": [("Oferta realizada", 1, 1), ("Oferta aceptada", 1, 1),
                             ("Reserva agendada", 1, 1), ("Reserva firmada", 1, 1)]},
    "Perfeccionado": {"sits": [("Documento privado", 1, 1), ("Documento notariado", 1, 1),
                               ("Documento registrado", 1, 0)]},
    "Inmueble entregado": {"sits": [("Inmueble entregado", 1, 1)]},
    "En espera": {"motivo": "Motivo de la espera (obligatorio)"},
    "Recuperado": {},
    "Perdido": {"motivo": "Motivo de pérdida (obligatorio)"},
}
MOTIVOS = ["No contesta", "Presupuesto fuera de mercado", "Zona", "Característica del inmueble",
           "Inmueble no disponible", "Captador no responde", "Propietario rechazó condiciones",
           "Uso no aplica", "Es asesor/colega", "Ya resolvió", "Cliente no acudió / desistió", "Otro"]
GEN_SITS = [("Buscando", 0, 0), ("Esperando", 0, 0)]
for _k, _d in ESTADOS.items():
    _d["sits"] = list(_d.get("sits", [])) + ([] if _k == "Perdido" else GEN_SITS)

# Reserva firmada pide subir el documento y avisa al líder: solo desde el Sistema de Clientes.
SOLO_EN_SISTEMA = {"Reserva firmada"}


def sit_def(e, s):
    for x in ESTADOS.get(e, {}).get('sits', []):
        if x[0] == s:
            return x
    return None


def validar(d):
    """Misma validación que el Sistema de Clientes. None si está bien; si no, el mensaje."""
    e = d.get('estado')
    s = (d.get('situacion') or '').strip()
    f = d.get('fecha') or ''
    if e not in ESTADOS:
        return 'Elige un estado'
    cfg = ESTADOS[e]
    if cfg.get('sits'):
        sd = sit_def(e, s)
        if not sd:
            return 'Elige la situación'
        if sd[2] and not re.match(r'^\d{4}-\d{2}-\d{2}', f):
            return f'Indica la fecha ({s})'
    if cfg.get('motivo'):
        m = (d.get('motivo') or '').strip()
        if m not in MOTIVOS:
            return 'Elige el detalle (motivo) de la lista'
        if m == 'Otro' and not (d.get('comentario') or '').strip():
            return 'Si eliges «Otro», escribe el Comentario'
    return None


def registro(d, *, asesor, at):
    """Arma el registro igual que /a/<token>/guardar del Sistema de Clientes (sin documento)."""
    es = d['estado']
    rec = {'asesor': asesor, 'estado': es, 'at': at}
    if ESTADOS[es].get('sits'):
        rec['situacion'] = (d.get('situacion') or '').strip()
    sd = sit_def(es, rec.get('situacion'))
    if sd and sd[1] and re.match(r'^\d{4}-\d{2}-\d{2}$', (d.get('fecha') or '')[:10]):
        rec['fecha'] = d['fecha'][:10]
    if ESTADOS[es].get('motivo'):
        rec['motivo'] = (d.get('motivo') or '').strip()[:500]
        rec['motivo_cat'] = rec['motivo']
    if (d.get('comentario') or '').strip():
        rec['comentario'] = d['comentario'].strip()[:2000]
    return rec


def opciones_js():
    """Estructura para el formulario de la tarjeta (situaciones por estado, si pide fecha o motivo)."""
    out = {}
    for e, cfg in ESTADOS.items():
        out[e] = {
            'sits': [{'s': s, 'fecha': bool(pf), 'oblig': bool(fo), 'solo_sistema': s in SOLO_EN_SISTEMA}
                     for s, pf, fo in cfg.get('sits', [])],
            'motivo': cfg.get('motivo') or '',
        }
    return out


def estatus_de(rec):
    """Valor que usa el embudo: situación si hay, si no el estado (igual que clientes_sync)."""
    return (rec.get('situacion') or '') or (rec.get('estado') or '')


def texto_corto(rec):
    partes = [rec.get('estado') or '']
    if rec.get('situacion'):
        partes.append(rec['situacion'])
    return ' · '.join(p for p in partes if p)
