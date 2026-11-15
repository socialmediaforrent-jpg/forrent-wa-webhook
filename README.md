# forrent-wa-webhook

Receptor de webhooks de **WhatsApp Cloud API** (Meta) para For Rent. Guarda mensajes y estados en Postgres (Render)
y los expone, con llave Bearer, a los scripts de la caja (`scripts/cloudapi/wa_inbox.py`, `wa_send.py`).

- `GET /health`, `GET /healthz`
- `GET /webhook` — verificación de Meta (`hub.mode`, `hub.verify_token`, `hub.challenge`)
- `POST /webhook` — eventos; valida `X-Hub-Signature-256` con `META_APP_SECRET` (si falta, acepta y registra un aviso)
- `GET /api/events?since_id=N`, `POST /api/outbound`, `GET /api/last_inbound?wa_id=` — requieren `Authorization: Bearer <WA_READ_KEY>`
- `GET /`, `GET /portal`, `GET /panel`, `GET /panel/data`, `GET /inventario` — portal For Rent (sesión en /login; `PANEL_PASSWORD_SHA256` para jose, `PORTAL_ASESORES_SHA256` para asesores)

Secretos: solo por variables de entorno en Render (`WA_VERIFY_TOKEN` o `WA_VERIFY_TOKEN_SHA256`,
`WA_READ_KEY` o `WA_READ_KEY_SHA256`, `META_APP_SECRET`, `PANEL_PASSWORD_SHA256`,
`PORTAL_ESTATUS_URL`, `PORTAL_CIERRES_URL`, `PORTAL_ASESORES_SHA256`). Nada de secretos en este repo.
Los asesores entran al mismo `/portal` con su usuario. Contraseñas en la caja (`secrets/portal_asesores/`); en Render solo el SHA256.
Los clientes propios se guardan en Postgres de este servicio, no en GHL.
La respuesta automática fuera de horario existe pero está apagada (`AUTO_REPLY_ENABLED=0`).
El inventario es una foto sanitizada (`data/inventario.json`): título, operación, tipo, zona, precio y código interno; sin captadores ni enlaces de Rex/Wasi.

## Correos del portal (rama redesign-v1)
Se registran en la tabla `email_outbox` (pantalla /admin/correos). `EMAIL_MODE=log` (por defecto) no envía nada;
`test` envía solo a `EMAIL_TEST_TO`; `smtp` envía de verdad (requiere `SMTP_USER` y `SMTP_APP_PASSWORD`, contraseña de aplicación de Gmail).
`ASSIGN_EMAIL_MODE=omitir` (por defecto) evita duplicar el aviso de asignación que ya envía la rutina de la caja.
