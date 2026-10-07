-- Per-stage cost logging and owner-approved budget overrides.
-- llm_call_log is append-only; the stage label is written at insert time.
ALTER TABLE llm_call_log ADD COLUMN stage TEXT;

-- An override is the ONLY way a run or a month can exceed its hard cap, and only the owner can create one.
CREATE TABLE budget_approval (
  id        TEXT PRIMARY KEY,
  at        TEXT NOT NULL,
  scope     TEXT NOT NULL CHECK (scope IN ('run','month')),
  period    TEXT NOT NULL,                 -- run id, or 'YYYY-MM' (UTC)
  extra_usd REAL NOT NULL CHECK (extra_usd > 0),
  reason    TEXT NOT NULL
);

CREATE TRIGGER budget_approval_owner_only_insert BEFORE INSERT ON budget_approval
BEGIN
  SELECT RAISE(ABORT, 'budget overrides can only be approved by the owner') WHERE current_actor() IS NOT 'owner';
END;
CREATE TRIGGER budget_approval_append_only_update BEFORE UPDATE ON budget_approval
BEGIN
  SELECT RAISE(ABORT, 'budget_approval is append-only');
END;
CREATE TRIGGER budget_approval_append_only_delete BEFORE DELETE ON budget_approval
BEGIN
  SELECT RAISE(ABORT, 'budget_approval is append-only');
END;
