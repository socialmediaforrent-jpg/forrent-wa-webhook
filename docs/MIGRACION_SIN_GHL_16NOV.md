# Migración sin GHL — rama `migracion-sin-ghl-16nov` (16-nov-2026)

Punto de restauración: etiqueta `v-pre-migracion-16nov` = `551969a` (main en producción).
Nada de esta rama está en producción. No cambia tablas existentes (solo lecturas nuevas y rutas nuevas).

## 1. Inventario de referencias a GHL en app.py (34 líneas con «ghl» en la rama; 31 en main)
**Conclusión: app.py ya no llama a GHL en ningún punto.** No hay URL de leadconnectorhq ni token GHL.
Las referencias son nombres de datos históricos (columnas, `source='ghl_import'`, tablas de respaldo) y
comentarios. Renombrarlas cambiaría estructuras de datos (prohibido sin necesidad), así que se conservan.

| Línea(s) en la rama | Qué es | Qué hace | Cómo queda |
|---|---|---|---|
| 162-164 | `bak_pre_ghl_import_*` | Respaldo único antes de importar el histórico GHL (`CREATE TABLE IF NOT EXISTS`) | Igual (es el punto de restauración del histórico) |
| 176, 197, 203 | `TABLA_ASESORES_GHL_WASI.csv`, `nombre_ghl` | Siembra de correos de asesoras si el perfil no tiene | Igual: es un CSV local, no GHL. En Render el archivo no existe y se usan los otros orígenes |
| 844 | `_already_live_event(... 'ghl_import')` | Evita duplicar mensajes vivos con el histórico | Igual |
| 859-939 | `/panel/import-ghl` (GET y POST) | Estado y carga del histórico GHL → `wa_events` (solo admin) | Igual + comentario: es dato guardado; tras el corte queda como consulta del histórico. `ghl_mensajes_loop.sh` (caja) es quien lo alimenta y se apaga en el corte |
| 1286-1290 | Comentario del portal-sync | Decía «registro de asignaciones GHL» | Corregido: clientes/teléfonos/productividad/cierres salen del CRM (`crm.portal_payload`); inventario y captaciones vienen de Wasi/Rex |
| 1413+ | Rutas nuevas `/admin/crm-read/*` | Reemplazan los GET a GHL de los scripts de la caja | Nuevo (solo lectura, llave PORTAL_SYNC_KEY) |
| 2352 | `maybe_auto_reply` | Respuesta automática fuera de horario (apagada, `AUTO_REPLY_ENABLED=0`) | Comentario corregido: reemplaza al workflow GHL; marca `auto=1` |

Otros módulos: `crm.py`, `crm_write.py`, `crm_import.py` usan la columna `ghl_id` (id histórico del contacto)
y los ids de los 15 campos personalizados como claves. Son datos, no conexión; se conservan.
`templates/propios.html` solo dice «No pasan por GHL».

## 2. Rutas nuevas (rama)
- `GET /admin/crm-read/contactos?desde&hasta&actualizados_desde&limite` · `GET /admin/crm-read/contacto/<id>` ·
  `GET /admin/crm-read/asignaciones?desde&hasta` · `GET /admin/crm-read/wa-nuevos?desde&hasta`
  Llave: cabecera `X-Portal-Sync-Key` (la misma de `/admin/crm-write`). Solo lectura. Contactos con la forma de GHL.
- `POST /admin/portal-links/import` (`{"texto", "dry_run": true por defecto, "verificar": true}`) y
  `GET /admin/portal-links/export`. Llave propia de Inventario `X-Inventario-Links-Key` validada contra
  `INVENTARIO_LINKS_KEY_SHA256` (no configurada = Inventario no entra). La llave de Inventario no abre CRM,
  portal-sync, Mensajes ni el portal (probado). Nunca borra; reemplazos quedan en `crm_portal_links_log`.

## 3. render.yaml
El Blueprint `exs-dbsu2mtg1s2s7387dvt0` tiene autoSync en `main`. El archivo decía `plan: free` para el
servicio y la base, pero en Render son Starter y basic-256mb (de pago). Se corrigió en la rama para que una
sincronización futura no intente bajarlos. Ojo: `AUTO_REPLY_ENABLED` y `GRAPH_VERSION` se reescriben en cada
sincronización con el valor del archivo.

## 4. Pruebas hechas (sin tocar producción)
Copia local de Postgres (dump del 15-nov + `crm_import` del export GHL del 15-nov) en la caja:
- crm-read: 403 sin llave o con llave mala; 400 con fechas inválidas; 404 contacto inexistente; datos correctos.
- Asignaciones del 15-nov desde el CRM: las 5 del archivo GHL + 1 más (Adriana, Ireida) que hay que revisar.
- Contactos nuevos del 15-nov: GHL 16 vs CRM 13, y el origen sale casi todo «otro» (el CRM no guarda
  atribuciones ni etiquetas de origen). El café debe seguir con GHL hasta el corte.
- Enlaces: la lista del 16-nov da 47 agregados + 23 sin cambio (igual a la carga de la mañana); segunda corrida
  70 sin cambio (idempotente); reemplazo registrado con URL anterior; dominio ajeno rechazado.

## 5. Rama `feat/bandeja-unificada` (16-nov-2026) — NO publicada
Punto de restauración: etiqueta `v-pre-bandeja-16nov` = `95d6564` (main en producción).
Todo es aditivo: tablas nuevas `crm_marketplace_links` y `crm_marketplace_links_log`; ninguna tabla existente cambia.

**Bandeja (Mensajes, `/panel`)**
- Botón «Responder» por WhatsApp: `POST /panel/responder` (sesión + CSRF). En el servidor: mismos permisos que
  `/panel` (admin ve todo; asesora/líder solo sus clientes asignados desde el 31-oct y sus propios), ventana de 24 h
  desde el último mensaje del cliente (nunca inicia, sin plantillas), filtro wa.me y teléfonos de asesores,
  máx. 20 envíos/min por usuario. El envío queda en `wa_events` como `outbound`, `source='portal'`, `data.por`.
- Solo envía si existen `META_WA_TOKEN` y `WA_PHONE_NUMBER_ID`; sin ellas la caja aparece deshabilitada con la razón.
- Instagram/Facebook: lectura por Graph API solo con `META_SOCIAL_INBOX=1` + `META_PAGE_TOKEN` (o `META_PAGE_TOKEN_FILE`).
  Solo admin (no hay forma segura aún de ligar un chat IG/FB al cliente de una asesora). Responder IG/FB: deshabilitado
  hasta que Meta apruebe la revisión de la app. Apagado por defecto.

**Rol Mercadeo** (usuario `mercadeo`, contraseña en `MERCADEO_PASSWORD_SHA256`; sin variable nadie entra)
- Solo abre `/mercadeo`, `/mercadeo/planificador`, `/mercadeo/link`, `/mercadeo/verificar`. Todo lo demás: 403 en el
  servidor (`MERCADEO_ENDPOINTS`). Administra enlaces de portales igual que Presidencia. No ve clientes ni mensajes.
- `/mercadeo/planificador`: lista (solo lectura) de publicaciones programadas que la caja sube por
  `/admin/portal-sync` con el nombre nuevo `planificador.json` (`{"posts": [...]}`). No publica nada.

**Mercadeo**: sección «Portales Inmobiliarios» (antes «10 portales», misma función) y nueva «Redes Sociales»
(Instagram, Facebook, TikTok, YouTube, LinkedIn + Facebook Marketplace). Enlaces en `data/redes_sociales.json`
(o `REDES_SOCIALES_JSON`); solo https del dominio de cada red; LinkedIn vacío (no hay perfil en los archivos).
Marketplace va por inmueble: `POST /mercadeo/marketplace`, solo rol admin, URL `facebook.com/marketplace/...`,
bitácora quién/cuándo/antes/después.

**Luz** pasa a rol `admin` en `data/asesores.json` (ve todos los clientes, mensajes e inmuebles, como José).

Pruebas: `python -m pytest tests/` (SQLite local, sin red). Variables nuevas a crear en Render al publicar:
`MERCADEO_PASSWORD_SHA256` (y opcionales `META_SOCIAL_INBOX`, `META_PAGE_TOKEN`).
