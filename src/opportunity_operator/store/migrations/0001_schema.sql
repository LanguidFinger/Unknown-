-- Opportunity Operator schema v1. Guards (triggers) are in 0002.
-- Dates are ISO-8601 text; NULL means UNKNOWN and is never guessed.

CREATE TABLE opportunity (
  id                TEXT PRIMARY KEY,
  canonical_key     TEXT NOT NULL UNIQUE,
  program_name      TEXT NOT NULL,
  sponsor           TEXT,
  primary_url       TEXT,
  instrument_type   TEXT CHECK (instrument_type IS NULL OR instrument_type IN
    ('grant','loan','investment','equity','reimbursement','tax_credit','prize','service_credit',
     'api_credit','contract','partnership','accelerator','other')),
  state             TEXT NOT NULL DEFAULT 'DISCOVERED' CHECK (state IN
    ('DISCOVERED','VERIFYING','OWNER_REVIEW','WATCHLIST','DISQUALIFIED','DECLINED','APPROVED',
     'PREPARING','READY_FOR_REVIEW','SUBMITTED','AWAITING_DECISION','AWARDED','NOT_AWARDED','WITHDRAWN')),
  status            TEXT CHECK (status IS NULL OR status IN ('open','upcoming','closed','rolling','unknown')),
  opens_on          TEXT,
  deadline          TEXT,
  award_min         INTEGER,
  award_max         INTEGER,
  award_typical     INTEGER,
  currency          TEXT NOT NULL DEFAULT 'USD',
  first_discovered  TEXT NOT NULL,
  last_verified     TEXT,
  next_review_at    TEXT,
  next_review_basis TEXT CHECK (next_review_basis IS NULL OR next_review_basis IN
    ('announced','historical','uncertain','rule')),
  supersedes_id     TEXT REFERENCES opportunity(id),
  tags              TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE opportunity_alias (
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  alias          TEXT NOT NULL,
  url            TEXT,
  PRIMARY KEY (opportunity_id, alias)
);

CREATE TABLE discovery_hit (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  at             TEXT NOT NULL,
  source         TEXT NOT NULL,
  query          TEXT,
  url            TEXT
);

-- Allowed lifecycle transitions. from_state '<none>' = creation. Seeded and then frozen in 0002.
CREATE TABLE state_transition (
  from_state        TEXT NOT NULL,
  to_state          TEXT NOT NULL,
  actors            TEXT NOT NULL,          -- ',agent,system,' style list
  requires_decision TEXT,                   -- owner decision type that must authorise it
  PRIMARY KEY (from_state, to_state)
);

CREATE TABLE decision (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  at             TEXT NOT NULL,
  actor          TEXT NOT NULL CHECK (actor = 'owner'),
  decision       TEXT NOT NULL CHECK (decision IN
    ('approve','decline','watch','request_changes','mark_submitted','withdraw','mark_awarded',
     'mark_not_awarded','reopen','snooze','legal_review_done','note')),
  reason_code    TEXT,
  notes          TEXT
);

CREATE TABLE status_event (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  at             TEXT NOT NULL,
  from_state     TEXT,
  to_state       TEXT NOT NULL,
  actor          TEXT NOT NULL CHECK (actor IN ('agent','owner','system')),
  reason         TEXT,
  assessment_id  TEXT,
  decision_id    TEXT REFERENCES decision(id)
);
CREATE UNIQUE INDEX status_event_decision_once ON status_event(decision_id) WHERE decision_id IS NOT NULL;
CREATE INDEX status_event_opp ON status_event(opportunity_id);

CREATE TABLE source_snapshot (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  url            TEXT NOT NULL,
  source_tier    INTEGER NOT NULL CHECK (source_tier BETWEEN 1 AND 8),
  content_type   TEXT,
  http_status    INTEGER,
  raw_sha256     TEXT NOT NULL,
  text_sha256    TEXT NOT NULL,
  raw_path       TEXT NOT NULL,
  text_path      TEXT NOT NULL,
  retrieved_at   TEXT NOT NULL
);

CREATE TABLE evidence (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  snapshot_id    TEXT NOT NULL REFERENCES source_snapshot(id),
  field          TEXT NOT NULL,
  value          TEXT,
  quote          TEXT NOT NULL CHECK (length(quote) > 0),
  quote_verified INTEGER NOT NULL CHECK (quote_verified IN (0,1)),
  quote_start    INTEGER,
  quote_end      INTEGER,
  confidence     TEXT CHECK (confidence IS NULL OR confidence IN ('high','medium','low')),
  extracted_by   TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  CHECK (quote_verified = 0 OR (quote_start IS NOT NULL AND quote_end IS NOT NULL AND quote_end > quote_start))
);
CREATE INDEX evidence_opp ON evidence(opportunity_id, field);

CREATE TABLE evidence_supersession (
  evidence_id   TEXT PRIMARY KEY REFERENCES evidence(id),
  superseded_by TEXT NOT NULL REFERENCES evidence(id),
  at            TEXT NOT NULL
);

CREATE TABLE assessment (
  id                     TEXT PRIMARY KEY,
  opportunity_id         TEXT NOT NULL REFERENCES opportunity(id),
  created_at             TEXT NOT NULL,
  gate_results           TEXT NOT NULL,
  value                  INTEGER CHECK (value IS NULL OR value BETWEEN 1 AND 10),
  fit                    INTEGER CHECK (fit IS NULL OR fit BETWEEN 1 AND 10),
  effort                 INTEGER CHECK (effort IS NULL OR effort BETWEEN 1 AND 10),
  probability_band       TEXT CHECK (probability_band IS NULL OR probability_band IN ('LOW','MEDIUM','HIGH','UNKNOWN')),
  probability_confidence TEXT,
  probability_basis      TEXT,
  ip_risk                TEXT CHECK (ip_risk IS NULL OR ip_risk IN ('Low','Medium','High','Unknown')),
  privacy_risk           TEXT CHECK (privacy_risk IS NULL OR privacy_risk IN ('Low','Medium','High','Unknown')),
  legal_review_required  INTEGER NOT NULL DEFAULT 0 CHECK (legal_review_required IN (0,1)),
  priority_score         REAL,
  recommendation         TEXT CHECK (recommendation IS NULL OR recommendation IN
    ('APPLY','INVESTIGATE','WATCH','LOW_PRIORITY','REJECT','LEGAL_REVIEW_REQUIRED')),
  why_it_deserves_attention TEXT,
  downgrade_reason       TEXT,
  rationale              TEXT,
  model_id               TEXT,
  prompt_version         TEXT,
  rules_version          TEXT
);

CREATE TABLE assessment_evidence (
  assessment_id TEXT NOT NULL REFERENCES assessment(id),
  evidence_id   TEXT NOT NULL REFERENCES evidence(id),
  PRIMARY KEY (assessment_id, evidence_id)
);

CREATE TABLE preference (
  id         TEXT PRIMARY KEY,
  key        TEXT NOT NULL,
  value      TEXT,
  origin     TEXT NOT NULL CHECK (origin IN ('owner_set','suggested','confirmed')),
  evidence   TEXT,
  active     INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE run_log (
  id          TEXT PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  stage       TEXT,
  domain      TEXT,
  tokens_in   INTEGER DEFAULT 0,
  tokens_out  INTEGER DEFAULT 0,
  cost_usd    REAL DEFAULT 0,
  fetches     INTEGER DEFAULT 0,
  status      TEXT,
  errors      TEXT
);

CREATE TABLE llm_call_log (
  id            TEXT PRIMARY KEY,
  run_id        TEXT,
  at            TEXT NOT NULL,
  model_id      TEXT NOT NULL,
  purpose       TEXT NOT NULL,
  destination   TEXT NOT NULL,
  prompt_sha    TEXT NOT NULL,
  prompt_text   TEXT NOT NULL,
  response_text TEXT,
  fact_ids      TEXT NOT NULL DEFAULT '[]',
  tokens_in     INTEGER,
  tokens_out    INTEGER,
  cost_usd      REAL
);

CREATE TABLE egress_log (
  id       TEXT PRIMARY KEY,
  run_id   TEXT,
  at       TEXT NOT NULL,
  kind     TEXT NOT NULL CHECK (kind IN ('fetch','api_post','search')),
  target   TEXT NOT NULL,
  body_sha TEXT,
  status   INTEGER,
  bytes    INTEGER
);

CREATE TABLE audit_incident (
  id          TEXT PRIMARY KEY,
  at          TEXT NOT NULL,
  channel     TEXT NOT NULL,
  term_hash   TEXT NOT NULL,
  context_sha TEXT
);

CREATE TABLE dossier (
  id             TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunity(id),
  version        INTEGER NOT NULL,
  created_at     TEXT NOT NULL,
  path           TEXT NOT NULL,
  facts_used     TEXT NOT NULL DEFAULT '[]',
  unknowns       TEXT NOT NULL DEFAULT '[]',
  UNIQUE (opportunity_id, version)
);

-- ===== Profiles. Writable only by the owner role; readable only via the context role. =====
CREATE TABLE owner_profile (
  key               TEXT PRIMARY KEY,
  value             TEXT,
  sensitivity       TEXT NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','high')),
  access_level      TEXT NOT NULL DEFAULT 'CONFIDENTIAL'
                    CHECK (access_level IN ('PUBLIC','APPLICATION_SAFE','CONFIDENTIAL')),
  source            TEXT,
  verified_by_owner INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_owner IN (0,1)),
  updated_at        TEXT
);

CREATE TABLE business_profile (
  key               TEXT PRIMARY KEY,
  value             TEXT,
  sensitivity       TEXT NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','high')),
  access_level      TEXT NOT NULL DEFAULT 'CONFIDENTIAL'
                    CHECK (access_level IN ('PUBLIC','APPLICATION_SAFE','CONFIDENTIAL')),
  source            TEXT,
  verified_by_owner INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_owner IN (0,1)),
  updated_at        TEXT
);

-- 'RESTRICTED' is intentionally NOT a storable level: restricted material has nowhere to live.
CREATE TABLE project (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  default_access_level TEXT NOT NULL DEFAULT 'CONFIDENTIAL'
                       CHECK (default_access_level IN ('PUBLIC','APPLICATION_SAFE','CONFIDENTIAL')),
  external_alias       TEXT,
  created_at           TEXT NOT NULL
);

CREATE TABLE project_fact (
  id                TEXT PRIMARY KEY,
  project_id        TEXT NOT NULL REFERENCES project(id),
  key               TEXT NOT NULL,
  value             TEXT,
  sensitivity       TEXT NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','high')),
  access_level      TEXT NOT NULL DEFAULT 'CONFIDENTIAL'
                    CHECK (access_level IN ('PUBLIC','APPLICATION_SAFE','CONFIDENTIAL')),
  verified_by_owner INTEGER NOT NULL DEFAULT 0 CHECK (verified_by_owner IN (0,1)),
  updated_at        TEXT,
  UNIQUE (project_id, key)
);

-- No description/content column exists by design: only a label and deny-terms.
CREATE TABLE restricted_stub (
  id         TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  deny_terms TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ===== Setup / prerequisite queue for the (not yet formed) new entity =====
CREATE TABLE prerequisite (
  key              TEXT PRIMARY KEY,
  label            TEXT NOT NULL,
  depends_on       TEXT NOT NULL DEFAULT '[]',
  status           TEXT NOT NULL DEFAULT 'not_started'
                   CHECK (status IN ('not_started','in_progress','done','blocked')),
  details_verified INTEGER NOT NULL DEFAULT 0 CHECK (details_verified IN (0,1)),
  notes            TEXT,
  updated_at       TEXT
);

CREATE TABLE opportunity_prerequisite (
  opportunity_id    TEXT NOT NULL REFERENCES opportunity(id),
  prerequisite_key  TEXT NOT NULL REFERENCES prerequisite(key),
  basis_evidence_id TEXT REFERENCES evidence(id),
  PRIMARY KEY (opportunity_id, prerequisite_key)
);

-- Seed catalogue. Requirements/paths are NOT yet verified against primary sources (details_verified=0).
-- Status starts not_started for everything: the new entity is treated as not yet established.
INSERT INTO prerequisite (key, label, depends_on, details_verified) VALUES
 ('entity_formed',          'New legal entity formed (clean entity; no reuse of any prior entity identity)', '[]', 0),
 ('ein_obtained',           'New EIN obtained for the new entity', '["entity_formed"]', 0),
 ('uei_obtained',           'UEI assigned to the new entity (expected via SAM.gov entity registration)', '["ein_obtained"]', 0),
 ('sam_registration_active','SAM.gov registration for the new entity completed and active', '["ein_obtained"]', 0),
 ('grants_gov_registration','Grants.gov organization registration / roles (if required by the program)', '["sam_registration_active"]', 0),
 ('sbir_gov_registration',  'SBIR.gov company registration (if required by the agency)', '["entity_formed"]', 0),
 ('vosb_status',            'Veteran-owned status established for the new entity (certification path to be verified)', '["entity_formed"]', 0),
 ('sdvosb_status',          'Service-disabled-veteran-owned status established for the new entity (path to be verified)', '["entity_formed"]', 0);
