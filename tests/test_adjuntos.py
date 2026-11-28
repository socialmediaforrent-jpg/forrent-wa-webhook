"""Adjuntos de ofertas y reservas: tipos, tamaño, permisos y persistencia en la base."""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_bandeja import cliente, csrf, appmod  # noqa: E402
import adjuntos  # noqa: E402

PDF = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'
JPEG = b'\xff\xd8\xff\xdb' + b'\x00' * 64
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def _pdf(name='oferta.pdf'):
    return (io.BytesIO(PDF), name, 'application/pdf')


def registrar_oferta(slug, tipo='realizada', cliente_n='Cliente Demo', extra_files=None, **fields):
    data = {
        'csrf': csrf(slug),
        'tipo': tipo,
        'cliente': cliente_n,
        'fecha': fields.get('fecha', '2026-11-27'),
        'inmueble': fields.get('inmueble', 'Rex DEMO-1'),
        'monto': fields.get('monto', 'USD 1.000'),
        'nota': fields.get('nota', 'Ejemplo de prueba'),
    }
    files = extra_files if extra_files is not None else {'adjuntos': [_pdf()]}
    # werkzeug: list of tuples under same key via data + content_type, or
    r = cliente(slug).post('/ofertas', data={**data, **files}, content_type='multipart/form-data',
                           follow_redirects=False)
    return r


def post_multipart(slug, path, fields, files):
    data = dict(fields)
    data.update(files)
    return cliente(slug).post(path, data=data, follow_redirects=False)


def test_formulario_ofertas_acepta_adjuntos():
    h = cliente('ana').get('/ofertas?t=realizadas').get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in h
    assert 'name="adjuntos"' in h
    assert 'Registrar oferta' in h


def test_registrar_oferta_con_pdf_y_abrirlo():
    r = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'realizada', 'cliente': 'Cliente PDF',
        'fecha': '2026-11-27', 'inmueble': 'Apto Demo', 'monto': 'USD 1.000', 'nota': 'con pdf',
    }, {'adjuntos': _pdf('carta.pdf')})
    assert r.status_code in (302, 303)
    loc = r.headers.get('Location') or ''
    assert loc.startswith('/ofertas/')
    oid = int(loc.rsplit('/', 1)[-1])
    det = cliente('ana').get(f'/ofertas/{oid}')
    h = det.get_data(as_text=True)
    assert det.status_code == 200
    assert 'Cliente PDF' in h and 'carta.pdf' in h
    assert f'/ofertas/{oid}/adjuntos/' in h
    # id del adjunto
    import re
    m = re.search(rf'/ofertas/{oid}/adjuntos/(\d+)', h)
    assert m
    aid = int(m.group(1))
    dl = cliente('ana').get(f'/ofertas/{oid}/adjuntos/{aid}')
    assert dl.status_code == 200
    assert dl.headers.get('Content-Type', '').startswith('application/pdf')
    assert dl.data.startswith(b'%PDF')
    down = cliente('ana').get(f'/ofertas/{oid}/adjuntos/{aid}?dl=1')
    assert down.status_code == 200
    cd = down.headers.get('Content-Disposition') or ''
    assert 'attachment' in cd and 'carta.pdf' in cd


def test_agregar_adjunto_a_oferta_existente():
    r = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'recibida', 'cliente': 'Erick Demo',
        'fecha': '2026-11-27', 'inmueble': 'Rex JF-AP97', 'monto': 'USD 90.000',
    }, {})  # sin archivo
    oid = int((r.headers.get('Location') or '').rsplit('/', 1)[-1])
    h = cliente('ana').get(f'/ofertas/{oid}').get_data(as_text=True)
    assert 'No hay archivos adjuntos' in h
    add = post_multipart('ana', f'/ofertas/{oid}/adjuntos', {'csrf': csrf('ana')},
                         {'adjuntos': (io.BytesIO(JPEG), 'foto.jpg', 'image/jpeg')})
    assert add.status_code in (302, 303)
    h2 = cliente('ana').get(f'/ofertas/{oid}').get_data(as_text=True)
    assert 'foto.jpg' in h2 and 'No hay archivos adjuntos' not in h2


def test_rechaza_tipo_y_tamano():
    r = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'recibida', 'cliente': 'Mal archivo', 'fecha': '2026-11-27',
    }, {'adjuntos': (io.BytesIO(b'MZ-not-a-pdf'), 'virus.exe', 'application/octet-stream')})
    assert r.status_code == 400
    assert 'Solo se aceptan PDF e imágenes' in r.get_data(as_text=True)
    grande = b'\xff\xd8\xff\xdb' + b'\x00' * (adjuntos.MAX_BYTES)
    r2 = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'recibida', 'cliente': 'Archivo grande', 'fecha': '2026-11-27',
    }, {'adjuntos': (io.BytesIO(grande), 'huge.jpg', 'image/jpeg')})
    assert r2.status_code == 400
    assert '5 MB' in r2.get_data(as_text=True)


def test_permisos_asesor_lider_admin():
    r = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'realizada', 'cliente': 'Privado Ana',
        'fecha': '2026-11-27', 'inmueble': 'X',
    }, {'adjuntos': _pdf('privado.pdf')})
    oid = int((r.headers.get('Location') or '').rsplit('/', 1)[-1])
    h = cliente('ana').get(f'/ofertas/{oid}').get_data(as_text=True)
    import re
    aid = int(re.search(rf'/ofertas/{oid}/adjuntos/(\d+)', h).group(1))
    # Asesora no ve registros de Ana.
    assert cliente('herlinda').get(f'/ofertas/{oid}').status_code == 403
    assert cliente('herlinda').get(f'/ofertas/{oid}/adjuntos/{aid}').status_code == 403
    # Admin sí ve y abre, y puede agregar.
    j = cliente('jose').get(f'/ofertas/{oid}')
    assert j.status_code == 200 and 'Privado Ana' in j.get_data(as_text=True)
    assert cliente('jose').get(f'/ofertas/{oid}/adjuntos/{aid}').status_code == 200
    add = post_multipart('jose', f'/ofertas/{oid}/adjuntos', {'csrf': csrf('jose')},
                         {'adjuntos': (io.BytesIO(PNG), 'nota.png', 'image/png')})
    assert add.status_code in (302, 303)
    # Admin no registra ofertas nuevas (regla previa).
    crear = post_multipart('jose', '/ofertas', {
        'csrf': csrf('jose'), 'tipo': 'recibida', 'cliente': 'Jose no', 'fecha': '2026-11-27',
    }, {})
    assert crear.status_code == 403


def test_reservas_con_adjunto_y_menu():
    mods = cliente('ana').get('/modulos').get_data(as_text=True)
    assert 'href="/reservas"' in mods
    h = cliente('ana').get('/reservas').get_data(as_text=True)
    assert h.count('name="adjuntos"') and 'multipart/form-data' in h
    r = post_multipart('ana', '/reservas', {
        'csrf': csrf('ana'), 'cliente': 'Reserva Demo', 'fecha': '2026-11-28',
        'inmueble': 'Casa Demo', 'monto': 'USD 2.000',
    }, {'adjuntos': _pdf('contrato.pdf')})
    assert r.status_code in (302, 303)
    loc = r.headers.get('Location') or ''
    assert loc.startswith('/reservas/')
    rid = int(loc.rsplit('/', 1)[-1])
    det = cliente('ana').get(f'/reservas/{rid}').get_data(as_text=True)
    assert 'contrato.pdf' in det and 'Reserva Demo' in det
    import re
    aid = int(re.search(rf'/reservas/{rid}/adjuntos/(\d+)', det).group(1))
    assert cliente('ana').get(f'/reservas/{rid}/adjuntos/{aid}').data.startswith(b'%PDF')
    assert cliente('herlinda').get(f'/reservas/{rid}').status_code == 403
    assert cliente('jose').get(f'/reservas/{rid}').status_code == 200


def test_eliminar_oferta_borra_adjuntos():
    r = post_multipart('ana', '/ofertas', {
        'csrf': csrf('ana'), 'tipo': 'recibida', 'cliente': 'Borrar Demo', 'fecha': '2026-11-27',
    }, {'adjuntos': _pdf('tmp.pdf')})
    oid = int((r.headers.get('Location') or '').rsplit('/', 1)[-1])
    # herlinda no puede borrar la de Ana
    bad = cliente('herlinda').post(f'/ofertas/{oid}/eliminar', data={'csrf': csrf('herlinda')})
    assert bad.status_code == 403
    ok = cliente('ana').post(f'/ofertas/{oid}/eliminar', data={'csrf': csrf('ana')}, follow_redirects=False)
    assert ok.status_code in (302, 303)
    assert cliente('ana').get(f'/ofertas/{oid}').status_code == 404
    assert not (appmod.q('SELECT id FROM operacion_adjuntos WHERE tipo=%s AND registro_id=%s',
                         ('oferta', oid), fetch=True) or [])


def test_adjuntos_mime_helper():
    assert adjuntos.mime_de(PDF) == 'application/pdf'
    assert adjuntos.mime_de(JPEG) == 'image/jpeg'
    assert adjuntos.mime_de(PNG) == 'image/png'
    assert adjuntos.mime_de(b'hola') is None
