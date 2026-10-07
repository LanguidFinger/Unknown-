"""A tiny stand-in pipeline (Phase 0 only) that exercises every control with mock providers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from opportunity_operator.app import App
from opportunity_operator.policy.access import Destination, Purpose
from opportunity_operator.text import canonical_text, html_to_text


class ExtractedItem(BaseModel):
    field: str
    value: str
    quote: str
    confidence: str = "medium"


class ExtractionOut(BaseModel):
    items: list[ExtractedItem]


class FitOut(BaseModel):
    fit: int
    rationale: str


def run(app: App, oid: str, url: str, *, fit_destination: Destination = Destination.CLOUD_LLM) -> dict[str, Any]:
    fetched = app.fetcher.get(url)
    sid = app.repo.save_snapshot(oid, fetched, source_tier=1)
    app.repo.transition(oid, "VERIFYING", "stub pipeline")
    page_text = canonical_text(html_to_text(fetched.body.decode("utf-8", errors="replace")))

    ext_prompt = app.builder.make_prompt(Purpose.EXTRACTION, Destination.CLOUD_LLM,
                                         "Extract deadline and award facts with verbatim quotes.",
                                         untrusted=[page_text], schema_name="ExtractionOut", stage="extract")
    extracted = app.llm.structured(ext_prompt, ExtractionOut).parsed
    ids, unverified = [], 0
    for item in extracted.items:
        rec = app.repo.add_evidence(oid, sid, item.field, item.value, item.quote, confidence=item.confidence,
                                    extracted_by=f"{app.settings.stage_models['extract']}/extract.v0")
        if rec.verified:
            ids.append(rec.id)
        else:
            unverified += 1

    fit_prompt = app.builder.make_prompt(Purpose.FIT_ASSESSMENT, fit_destination,
                                         "Rate fit 1-10 for the listed project given verified evidence.",
                                         untrusted=[e["quote"] for e in app.repo.verified_evidence(oid)],
                                         schema_name="FitOut", stage="judge")
    fit = app.llm.structured(fit_prompt, FitOut).parsed
    aid = app.repo.add_assessment(oid, gate_results={"stub": "pass"}, evidence_ids=ids, fit=max(1, min(10, fit.fit)),
                                  rationale=fit.rationale, recommendation="INVESTIGATE", model_id="mock", prompt_version="v0")
    app.repo.transition(oid, "OWNER_REVIEW", "stub pipeline complete", assessment_id=aid)

    # Dossier: facts rendered into application-facing output use the APPLICATION_OUTPUT ceiling.
    out_facts = app.builder.facts(Purpose.DRAFTING, Destination.APPLICATION_OUTPUT)
    body = ["# Dossier", f"opportunity: {oid}", "", "## Evidence"]
    body += [f"- {e['field']}: {e['value']} — \"{e['quote']}\" ({e['source_url']})" for e in app.repo.verified_evidence(oid)]
    body += ["", "## Profile facts used"] + [f"- {f.label}.{f.key}: {f.value}" for f in out_facts]
    app.datadir.put_text(f"dossiers/{oid}/dossier.md", "\n".join(body))
    return {"snapshot": sid, "evidence_ids": ids, "unverified": unverified, "assessment": aid}
