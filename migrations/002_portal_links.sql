-- Mercadeo de portales (rama mercadeo-portales). SOLO tablas nuevas; no altera ninguna existente.
-- La app también las crea sola al arrancar (portales.init). Restauración: DROP TABLE crm_portal_links, crm_portal_links_log;
CREATE TABLE IF NOT EXISTS crm_portal_links (
  id BIGSERIAL PRIMARY KEY,
  inmueble_key TEXT NOT NULL,          -- 'c:<código Wasi/Rex>' o 'm:<origen|operación|tipo|zona|precio>' si no hay código
  inmueble_codigo TEXT,
  inmueble_match TEXT,
  portal TEXT NOT NULL,                -- mercadolibre, rex, wasi, bienesonline, agentiz, housy, anuto, propiedash, zonaven, encuadras
  url TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'pendiente',  -- ok | protegido | caido | pendiente
  http_status INTEGER,
  verificado_en TEXT,
  ultimo_ok_en TEXT,
  fuente TEXT,
  nota TEXT,
  creado_por TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (inmueble_key, portal)
);
CREATE INDEX IF NOT EXISTS crm_portal_links_portal ON crm_portal_links (portal, estado);
CREATE TABLE IF NOT EXISTS crm_portal_links_log (
  id BIGSERIAL PRIMARY KEY, inmueble_key TEXT NOT NULL, portal TEXT NOT NULL, accion TEXT NOT NULL,
  url_antes TEXT, url_despues TEXT, por TEXT, at TEXT NOT NULL
);
