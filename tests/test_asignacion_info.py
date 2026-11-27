"""Parser de «Información del cliente»: ordena el texto de la asignación sin inventar ni perder datos."""
import asignacion_info


# Texto sintético con la misma forma que una asignación real (códigos, captador, OJO, frases, WhatsApp).
TEXTO = """
Búsqueda de ALQUILER por rueda Búsqueda/Visita a Ana I. Teixeira (reel 19 IG AbcDefGhijk). Cliente directo.
Pidió información de los de 3 habitaciones en La Florida y Los Caobos; El Bosque «también puede ser». Presupuesto US$750 al mes. Los 6 meses los cubre sin problema.

NO ofrecer La Florida Rex 5088234635 (115 m², US$850, #13 del reel): no está activo (Inventario: Rex dijo que no existe o no está activo el 18-nov). OJO: al cliente NO se le ha mandado la corrección; Ana debe aclarárselo. No se le escribió nada a Carla en esta asignación.

Puede retomar Los Caobos Rex 3927474542 (3 hab, 91 m², US$800). Captadora (SOLO asesora): Tania Ejemplo, +58 414-0002222, tania.ejemplo@example.com. Link: https://rexup.net/AAAAAAA/ver_inmueble_agentes.php?cod_inmueble=3927474542. Alianza no confirmada.

Puede retomar El Bosque Wasi 10524405 (3 hab, 105 m², US$840): Disponible. Captador en ficha Wasi hoy: GUSTAVO DEMO. Datos en sistema (SOLO asesora): Gustavo Demo, RE/MAX Escala, +58 414-0003333, gustavo.demo@example.com. Link: https://info.wasi.co/x/10524405. Alianza no confirmada.

Mónica también le ofreció El Paraíso, 3 hab, 112 m², US$750. Código: Wasi 10500403. Ficha Wasi: Disponible. Datos en sistema (SOLO asesora): Pedro Demo, Abitu Inmobiliaria, +58 412-0004444, pedro.demo@example.com. Alianza no confirmada. Link: https://info.wasi.co/x/10500403.

Aún no dijo si acepta llamada ni a qué hora (se lo preguntó el 26-nov a las 9:03 AM y no ha respondido). Ana debe escribirle por WhatsApp y confirmarlo. En GHL figuraba como «Carla»; se actualizó a Carla Demo (ella dio el nombre). No se le dio el teléfono de la asesora.
WhatsApp +58 414-0001111.
Frases: «¡Hola! Quiero más información sobre el de 3 habitaciones en la Florida y el de los Caobos» «El del bosque también puede ser» «Claro, mi nombre es Carla Demo. Mi presupuesto puede cubrir 750 queria saber si era negociable el precio o ese era el precio definitivo. Los 6 meses puedo cubrirlos sin problema».
"""


def _val(filas, label):
    for x in filas or []:
        if x.get('label') == label:
            return x.get('value')
    return None


def test_resumen_busca_y_frases():
    info = asignacion_info.parse_asignacion({
        'inmueble': TEXTO, 'operacion': 'Alquiler', 'tipo_lead': 'Búsqueda',
    })
    assert _val(info['resumen'], 'Operación') == 'Alquiler'
    assert _val(info['resumen'], 'Tipo de lead') == 'Búsqueda'
    assert 'Búsqueda/Visita' in (_val(info['resumen'], 'Rueda') or '')
    assert 'Reel 19 IG AbcDefGhijk' in (_val(info['resumen'], 'Origen') or '')
    assert _val(info['resumen'], 'Cliente directo') == 'Sí'
    assert 'La Florida' in (_val(info['busca'], 'Zonas') or '')
    assert _val(info['busca'], 'Habitaciones') == '3'
    assert 'US$750' in (_val(info['busca'], 'Presupuesto') or '')
    assert '6 meses' in (_val(info['busca'], 'Plazo') or '')
    assert info['whatsapp_texto'] == '+58 414-0001111'
    assert any('negociable' in f.lower() for f in info['frases'])
    assert any('bosque también puede ser' in f.lower() for f in info['frases'])


def test_inmuebles_codigos_badges_captador_y_links():
    info = asignacion_info.parse_asignacion({'inmueble': TEXTO})
    assert len(info['inmuebles']) == 4
    por = {i['titulo']: i for i in info['inmuebles']}
    fl = por['La Florida']
    assert fl['codigos'][0]['label'] == 'Rex 5088234635'
    assert 'NO ofrecer' in fl['badges']
    assert fl['precio'] == 'US$850'
    assert fl['m2'] == '115'
    # «Rex dijo» no se toma como código (tiene que llevar un dígito en el identificador).
    assert all('dijo' not in (c.get('codigo') or '').lower() for i in info['inmuebles'] for c in i.get('codigos') or [])

    ca = por['Los Caobos']
    assert ca['codigos'][0]['codigo'] == '3927474542'
    assert 'Alianza no confirmada' in ca['badges']
    cap = ca['captador']
    assert cap['nombre'] == 'Tania Ejemplo'
    assert cap['telefono'] == '+58 414-0002222'
    assert cap['email'] == 'tania.ejemplo@example.com'
    assert cap['solo_asesor'] is True
    assert any(l['label'] == 'Ver en Rex' and '3927474542' in l['url'] for l in ca['links'])

    bo = por['El Bosque']
    assert bo['codigos'][0]['label'] == 'Wasi 10524405'
    assert 'Disponible' in bo['badges']
    assert bo['captador']['email'] == 'gustavo.demo@example.com'
    assert any('wasi.co' in l['url'] for l in bo['links'])

    pa = por['El Paraíso']
    assert pa['codigos'][0]['codigo'] == '10500403'
    assert pa['precio'] == 'US$750'  # sin punto colgante
    assert pa['captador']['email'] == 'pedro.demo@example.com'


def test_ojos_otras_notas_no_pierde_la_nota_de_llamada():
    info = asignacion_info.parse_asignacion({'inmueble': TEXTO})
    assert any('corrección' in o.lower() for o in info['ojos'])
    notas = ' | '.join(info['otras_notas'])
    assert 'acepta llamada' in notas
    assert 'escribirle por WhatsApp' in notas
    assert 'Carla Demo' in notas
    assert 'teléfono de la asesora' in notas


def test_vacio_no_inventa():
    info = asignacion_info.parse_asignacion({'inmueble': '', 'operacion': '', 'tipo_lead': ''})
    assert info['inmuebles'] == []
    assert info['ojos'] == []
    assert info['frases'] == []
    assert info['otras_notas'] == []
    assert not any(x.get('value') for x in info['resumen'] if x['label'] == 'Cliente directo')


def test_resumen_corto_no_vacia_si_hay_texto():
    r = asignacion_info.resumen_corto({'inmueble': 'Busca apto en La Florida, 3 habitaciones, US$750.'})
    assert r and 'La Florida' in r
