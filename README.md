# forrent-wa-webhook

Receptor de webhooks de **WhatsApp Cloud API** (Meta) para For Rent. Guarda mensajes y estados en Postgres (Render)
y los expone, con llave Bearer, a los scripts de la caja (`scripts/cloudapi/wa_inbox.py`, `wa_send.py`).

- `GET /health`, `GET /healthz`
- `GET /webhook` — verificación de Meta (`hub.mode`, `hub.verify_token`, `hub.challenge`)
- `POST /webhook` — eventos; valida `X-Hub-Signature-256` con `META_APP_SECRET` (si falta, acepta y registra un aviso)
- `GET /api/events?since_id=N`, `POST /api/outbound`, `GET /api/last_inbound?wa_id=` — requieren `Authorization: Bearer <WA_READ_KEY>`
- `GET /panel`, `GET /panel/data` — bandeja de entrada solo lectura (HTTP Basic, usuario `jose`; `PANEL_PASSWORD_SHA256`)

Secretos: solo por variables de entorno en Render (`WA_VERIFY_TOKEN` o `WA_VERIFY_TOKEN_SHA256`,
`WA_READ_KEY` o `WA_READ_KEY_SHA256`, `META_APP_SECRET`, `PANEL_PASSWORD_SHA256`). Nada de secretos en este repo.
La respuesta automática fuera de horario existe pero está apagada (`AUTO_REPLY_ENABLED=0`).
