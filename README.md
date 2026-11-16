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
Los correos iniciales de `asesor_perfil` se cargan (solo si el perfil no tiene correo) desde `TABLA_ASESORES_GHL_WASI.csv`, `config/asesores_whatsapp.json` y `asesores_emails_iniciales.json`; la asesora puede editarlos después.
Las solicitudes de captación las aprueba el líder del equipo (José puede aprobar todas) en `/aprobaciones`. Al aprobar se avisa a la asesora y a `socialmediaforrent@gmail.com` para cargar el inventario; el snapshot de inventario no se modifica solo.

## Mercadeo de portales (rama mercadeo-portales)
`/mercadeo`: cada inmueble con 10 casillas en este orden: Mercado Libre, Rex, Wasi, Bienes Online, Agentiz, Housy, Anuto,
Propiedash, ZonaVen y Encuadras. Logo = publicado con enlace verificado; casilla vacía = no publicado. Cada logo abre
Ir / Copiar / Compartir (Web Share API; si no está, WhatsApp o copiar). Asesora: sus captaciones; líder: las suyas y las
de su equipo; Presidencia: todo el inventario, más el lugar del planificador. Solo Presidencia agrega, cambia o quita
enlaces (`POST /mercadeo/link`, con CSRF; solo https y solo el dominio del portal) y lanza `POST /mercadeo/verificar`.
Datos en `crm_portal_links` (+ `crm_portal_links_log`), tablas nuevas (ver `migrations/002_portal_links.sql`).
`portal_links_import.py` (se corre en la caja) carga solo enlaces reales: ML activos del panel del día, fichas Wasi y Rex
del inventario diario. Estados: `ok` (respondió), `protegido` (el portal bloquea robots y el enlace viene de fuente
verificada), `caido` (dejó de responder; no se muestra como publicado), `pendiente`, `ajena` (ficha con datos de otro
captador, p. ej. la ficha Rex del aliado; nunca se muestra). Solo se muestran publicaciones nuestras (propias y de alianza con
nuestros datos y el aviso «solo cliente directo»), así que Ir, Copiar y Compartir quedan libres en los 10 logos. Wasi acepta solo
nuestro sitio (grupoinmobiliarioforrent.com). Sin GHL. Logos locales en `static/portales/`.
