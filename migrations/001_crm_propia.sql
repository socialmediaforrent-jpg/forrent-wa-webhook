-- CRM propia (fase 1, paso 1). SOLO tablas nuevas: no altera wa_events ni el resto de tablas existentes.
-- Corte ultra-permanente: los contactos anteriores al 31-oct-2026 se conservan; el portal los oculta.

CREATE TABLE IF NOT EXISTS crm_contacts (
  id BIGSERIAL PRIMARY KEY,
  ghl_id TEXT UNIQUE,
  phone TEXT,
  phone_normalized TEXT,
  phone_alt TEXT,
  email TEXT,
  first_name TEXT,
  last_name TEXT,
  name TEXT,
  source TEXT,
  source_kind TEXT NOT NULL DEFAULT 'ghl',
  country TEXT,
  city TEXT,
  state TEXT,
  address1 TEXT,
  postal_code TEXT,
  date_added TIMESTAMPTZ,
  date_updated TIMESTAMPTZ,
  assigned_to_ghl TEXT,
  -- 15 campos personalizados de GHL
  zonas_interes TEXT,
  subtipo_enlace TEXT,
  presupuesto_usd TEXT,
  estatus_ghl TEXT,
  tipo_requerimiento TEXT,
  agregado_por_lista TEXT,
  estado_enlace TEXT,
  tipo_de_cliente JSONB,
  operacion_ghl TEXT,
  whatsapp_asesor TEXT,
  propiedad_interes TEXT,
  tipo_lead TEXT,
  detalles_requerimiento TEXT,
  agregado_por TEXT,
  asesor_asignado TEXT,
  custom_fields JSONB,
  -- datos del registro / portal
  asesor_slug TEXT,
  asesor_nombre TEXT,
  asignado_at_raw TEXT,
  asignado_at TIMESTAMPTZ,
  estado TEXT,
  situacion TEXT,
  estatus TEXT,
  tg_estado TEXT,
  inmueble TEXT,
  operacion TEXT,
  origen TEXT,
  in_registro BOOLEAN NOT NULL DEFAULT FALSE,
  excluido BOOLEAN NOT NULL DEFAULT FALSE,
  propio_id TEXT,
  registro_id TEXT UNIQUE,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  registro JSONB
);

CREATE INDEX IF NOT EXISTS crm_contacts_phone_idx ON crm_contacts (phone_normalized);
CREATE INDEX IF NOT EXISTS crm_contacts_asesor_idx ON crm_contacts (asesor_slug);
CREATE INDEX IF NOT EXISTS crm_contacts_asignado_idx ON crm_contacts (asignado_at);
CREATE INDEX IF NOT EXISTS crm_contacts_registro_idx ON crm_contacts (in_registro) WHERE in_registro;
CREATE INDEX IF NOT EXISTS crm_contacts_regid_idx ON crm_contacts (registro_id);

CREATE TABLE IF NOT EXISTS crm_tags (
  id BIGSERIAL PRIMARY KEY,
  ghl_id TEXT UNIQUE,
  name TEXT NOT NULL UNIQUE,
  color TEXT,
  category_id TEXT
);

CREATE TABLE IF NOT EXISTS crm_contact_tags (
  contact_id BIGINT NOT NULL REFERENCES crm_contacts(id) ON DELETE CASCADE,
  tag_id BIGINT NOT NULL REFERENCES crm_tags(id) ON DELETE CASCADE,
  PRIMARY KEY (contact_id, tag_id)
);

CREATE TABLE IF NOT EXISTS crm_notes (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  ghl_id TEXT UNIQUE,
  ghl_contact_id TEXT,
  body TEXT,
  created_by TEXT,
  pinned BOOLEAN,
  created_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS crm_notes_contact_idx ON crm_notes (contact_id);

CREATE TABLE IF NOT EXISTS crm_assignments (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  ghl_id TEXT,
  asesor_nombre TEXT,
  asesor_slug TEXT,
  tipo_lead TEXT,
  assigned_at TIMESTAMPTZ,
  source TEXT,
  rueda_tipo TEXT,
  rueda_index INTEGER,
  note TEXT,
  payload JSONB
);
CREATE INDEX IF NOT EXISTS crm_assignments_contact_idx ON crm_assignments (contact_id);
CREATE INDEX IF NOT EXISTS crm_assignments_ghl_idx ON crm_assignments (ghl_id);

CREATE TABLE IF NOT EXISTS crm_rueda_state (
  tipo TEXT PRIMARY KEY,
  state_key TEXT NOT NULL,
  indice INTEGER NOT NULL DEFAULT 0,
  orden JSONB NOT NULL DEFAULT '[]',
  pausados JSONB,
  updated_at TIMESTAMPTZ,
  notas JSONB,
  raw JSONB
);

CREATE TABLE IF NOT EXISTS crm_phase_events (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE CASCADE,
  ghl_id TEXT,
  estado TEXT,
  situacion TEXT,
  estatus TEXT,
  at TIMESTAMPTZ,
  source TEXT,
  payload JSONB
);
CREATE INDEX IF NOT EXISTS crm_phase_events_contact_idx ON crm_phase_events (contact_id);

CREATE TABLE IF NOT EXISTS crm_respuestas (
  ghl_id TEXT PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  payload JSONB NOT NULL,
  updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS crm_visits (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  ghl_id TEXT,
  asesor_slug TEXT,
  fecha TEXT,
  hora TEXT,
  cliente TEXT,
  inmueble TEXT,
  nota TEXT,
  resultado TEXT,
  created_at TIMESTAMPTZ,
  source TEXT,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS crm_offers (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  ghl_id TEXT,
  asesor_slug TEXT,
  tipo TEXT,
  cliente TEXT,
  inmueble TEXT,
  monto TEXT,
  moneda TEXT,
  fecha TEXT,
  nota TEXT,
  created_at TIMESTAMPTZ,
  source TEXT,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS crm_reservations (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  ghl_id TEXT,
  asesor_slug TEXT,
  asesor_nombre TEXT,
  fecha TEXT,
  operacion TEXT,
  inmueble TEXT,
  codigo TEXT,
  wasi TEXT,
  origen TEXT,
  estado TEXT,
  cliente TEXT,
  comision JSONB,
  source TEXT,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS crm_cierres (
  id TEXT PRIMARY KEY,
  asesor_slug TEXT,
  fecha TEXT,
  operacion TEXT,
  inmueble TEXT,
  codigo TEXT,
  origen TEXT,
  estado_pago TEXT,
  cliente TEXT,
  fuente TEXT,
  calc JSONB,
  prueba BOOLEAN DEFAULT FALSE,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS crm_opportunities (
  id BIGSERIAL PRIMARY KEY,
  ghl_id TEXT UNIQUE,
  contact_id BIGINT REFERENCES crm_contacts(id) ON DELETE SET NULL,
  pipeline_id TEXT,
  pipeline_name TEXT,
  stage_id TEXT,
  stage_name TEXT,
  name TEXT,
  status TEXT,
  monetary_value NUMERIC,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS crm_meta (
  key TEXT PRIMARY KEY,
  value JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
