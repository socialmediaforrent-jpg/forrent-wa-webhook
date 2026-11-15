"""Correos del portal: bandeja de salida (outbox) + remitente intercambiable.

EMAIL_MODE (env):
  log   (por defecto) -> solo registra en email_outbox con estado 'registrado'. No envía nada.
  test  -> envía solo a EMAIL_TEST_TO (dirección de prueba); el destinatario real queda en el registro.
  smtp  -> envía de verdad por SMTP (Gmail). Requiere SMTP_USER y SMTP_APP_PASSWORD. No usar sin autorización.
ASSIGN_EMAIL_MODE: 'omitir' (por defecto) evita duplicar el aviso de asignación que ya envía la rutina de la caja.
Nunca registra contraseñas.
"""
import os, smtplib, ssl
from email.message import EmailMessage

FROM_ADDR = os.environ.get('EMAIL_FROM', 'socialmediaforrent@gmail.com')
ADMIN_NOTIFY = os.environ.get('ADMIN_NOTIFY_EMAIL', 'socialmediaforrent@gmail.com')


def mode():
    m = (os.environ.get('EMAIL_MODE') or 'log').strip().lower()
    return m if m in ('log', 'test', 'smtp') else 'log'


def _smtp_send(to_addr, subject, body):
    user, pwd = os.environ.get('SMTP_USER'), os.environ.get('SMTP_APP_PASSWORD')
    if not (user and pwd):
        raise RuntimeError('faltan SMTP_USER/SMTP_APP_PASSWORD')
    msg = EmailMessage()
    msg['From'] = f'For Rent Online <{FROM_ADDR}>'
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(os.environ.get('SMTP_HOST', 'smtp.gmail.com'), int(os.environ.get('SMTP_PORT', '465')),
                          context=ssl.create_default_context(), timeout=20) as s:
        s.login(user, pwd)
        s.send_message(msg)


def queue(q, now_iso, *, to_slug, to_addr, subject, body, evento, estado=None, motivo=''):
    """Registra el correo y, según EMAIL_MODE, lo envía. Devuelve el estado final."""
    m = mode()
    to_addr = (to_addr or '').strip()
    if estado is None:
        if not to_addr:
            estado, motivo = 'sin_correo', 'el perfil no tiene correo'
        elif m == 'log':
            estado = 'registrado'
        else:
            dest = os.environ.get('EMAIL_TEST_TO', '').strip() if m == 'test' else to_addr
            if not dest:
                estado, motivo = 'error', 'EMAIL_TEST_TO no configurado'
            else:
                try:
                    _smtp_send(dest, subject, body)
                    estado = 'enviado_prueba' if m == 'test' else 'enviado'
                except Exception as e:  # sin detalles sensibles
                    estado, motivo = 'error', type(e).__name__
    q("INSERT INTO email_outbox (to_slug, to_addr, asunto, cuerpo, evento, estado, motivo, created_at) "
      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (to_slug, to_addr, subject, body, evento, estado, motivo, now_iso()))
    return estado
