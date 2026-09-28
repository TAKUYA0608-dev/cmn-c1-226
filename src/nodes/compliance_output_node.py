"""ComplianceOutputNode (step 6) — main slot, step 5 (final synthesis).

Assemble the caller-facing result from the classification, retrieved statutes,
obligations, and gaps:

  - `answer`            cited prose (inline [<statute> <article>] markers; LLM-generated)
  - `citations`         JSON list mapping markers → statute records
  - `gap_checklist`     actionable items derived from gaps
  - `deadline_calendar` per-obligation effective dates (KB data — not hard-coded)
  - `conflicts`         JP↔EU risk_class divergences, surfaced (not collapsed — B-2)
  - `disclaimer`        mandatory "not legal advice" notice (+ human-review note when set)
  - `requires_human_review` finalized per the C-1 policy

Zero retrieval hits ⇒ structured out-of-scope response (no obligations asserted).
Hits but NO LLM bound ⇒ `LLM_NOT_CONFIGURED` (SUCCESS + error_code): the deterministic
citations / gap checklist / deadline calendar / conflicts / disclaimer are still
produced, the prose answer is not — never a stub reply in place of a synthesis.
The S-3 citation gate (CitationValidateNode) downstream enforces that every claim
in `answer` carries a statute/article citation.
"""

from __future__ import annotations

import json
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import (
    CONFIDENCE_THRESHOLD,
    HIGH_STAKES_CLASSES,
    LLM_NOT_CONFIGURED,
    LLMClient,
)
from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel

_PERSONA = (
    "You are an enterprise AI-regulation compliance analyst. Answer the question "
    "using ONLY the statutes below. Every sentence that states a regulatory "
    "obligation, classification, or deadline MUST cite its statute inline as "
    "[<statute_id> <article>]. Do not invent statutes or articles."
)
_DISCLAIMER = (
    "This response is generated information for compliance triage, NOT legal advice. "
    "Verify against the latest statute amendments and do not rely on it as the sole "
    "basis for a compliance decision; consult a qualified compliance officer or lawyer."
)
_HUMAN_REVIEW_NOTE = (
    " ⚠ This classification requires human review (low confidence or high-risk/"
    "prohibited class) before any final compliance determination."
)
_OUT_OF_SCOPE = (
    "This question falls outside the current regulatory knowledge base (Japan AI "
    "Regulation 2026 / EU AI Act / APPI 2026 / METI Guidelines for the JP / EU "
    "jurisdictions). I can't ground an answer in a statute, so I'm not asserting any "
    "obligations. Please rephrase toward a covered jurisdiction/topic or consult a "
    "compliance officer."
)


def _marker(rec: dict[str, Any]) -> str:
    return f"{rec.get('statute_id', '')} {rec.get('article', '')}".strip()


class ComplianceOutputNode(FunctionNode):
    """Synthesize the cited answer + checklist + calendar + conflicts + disclaimer."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__()
        # None = no LLM bound. Resolved by the graph (explicit kw > config["llm"]);
        # a bare node stays unbound and degrades rather than answering from a stub.
        self._llm: LLMClient | None = llm_client

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}

        hit_count = state.get("retrieval_hit_count") or 0
        try:
            statutes = json.loads(state.get("retrieved_statutes") or "[]")
        except (json.JSONDecodeError, TypeError):
            statutes = []

        if hit_count <= 0 or not statutes:
            return {
                "answer": _OUT_OF_SCOPE,
                "citations": json.dumps([], ensure_ascii=False),
                "gap_checklist": json.dumps([], ensure_ascii=False),
                "deadline_calendar": json.dumps([], ensure_ascii=False),
                "conflicts": json.dumps([], ensure_ascii=False),
                "disclaimer": _DISCLAIMER,
                "status": AgentStatus.SUCCESS.value,
            }

        try:
            classification = json.loads(state.get("classification") or "{}")
        except (json.JSONDecodeError, TypeError):
            classification = {}
        try:
            gaps = json.loads(state.get("gaps") or "[]")
        except (json.JSONDecodeError, TypeError):
            gaps = []
        per_j = classification.get("per_jurisdiction", []) if isinstance(classification, dict) else []

        # Citation set: each statute → a marker; back-fill classification basis_citations.
        # ``official_ref`` (verifiable official identifier — CELEX / statute
        # number / e-Gov id) rides every citation so the caller can verify the
        # legal fact independently. Empty when the bound KB record carries
        # none; the default seed always does (enforced by its provenance
        # test), so bare-Graph() runs are always verifiable.
        citations = [
            {
                "marker": _marker(rec),
                "statute_id": rec.get("statute_id", ""),
                "article": rec.get("article", ""),
                "jurisdiction": rec.get("jurisdiction", ""),
                "official_ref": rec.get("official_ref", ""),
            }
            for rec in statutes
        ]
        ctx_lines = [
            f"[{_marker(r)}] ({r.get('jurisdiction','')}, {r.get('law_type','obligation')}) "
            f"{r.get('title','')}: {r.get('text','')}"
            for r in statutes
        ]

        if self._llm is None:
            # Statutes were retrieved but nothing can synthesize the prose answer.
            # Say so (named code); keep the deterministic artefacts below.
            emit_trace_event(
                "llm_not_configured",
                {"reason_code": LLM_NOT_CONFIGURED, "citation_count": len(citations)},
                state,
            )
            answer = None
        else:
            prompt = (
                f"{_PERSONA}\n\n=== Statutes ===\n"
                + "\n".join(ctx_lines)
                + f"\n\n=== Classification ===\n{json.dumps(per_j, ensure_ascii=False)}"
                f"\n\n=== Question ===\n{state.get('validated_question') or state.get('question') or ''}"
            )
            answer = self._llm.generate(prompt)

        # Gap checklist (actionable) + deadline calendar (KB effective dates).
        gap_checklist = [
            {
                "item": g.get("obligation", ""),
                "basis_citation": g.get("basis_citation", ""),
                "jurisdiction": g.get("jurisdiction"),
                "severity": g.get("severity", "high"),
                "status": g.get("status", "unknown"),
            }
            for g in gaps
        ]
        deadline_calendar = [
            {
                "obligation": r.get("title") or r.get("text", "")[:80],
                "effective_date": r.get("effective_date", ""),
                "jurisdiction": r.get("jurisdiction", ""),
                "basis_citation": _marker(r),
            }
            for r in statutes
            if r.get("effective_date")
        ]

        # Surface JP↔EU divergence (do NOT collapse to one side — B-2).
        conflicts = _conflicts(per_j)

        # Finalize human-review flag (C-1): provisional flag OR any high-stakes / low-confidence.
        final_review = bool(state.get("requires_human_review")) or any(
            (c.get("confidence", 1.0) < CONFIDENCE_THRESHOLD) or (c.get("risk_class") in HIGH_STAKES_CLASSES)
            for c in per_j
        )
        disclaimer = _DISCLAIMER + (_HUMAN_REVIEW_NOTE if final_review else "")

        emit_trace_event(
            "compliance_output",
            {
                "citation_count": len(citations),
                "gap_count": len(gap_checklist),
                "conflict_count": len(conflicts),
                "requires_human_review": final_review,
            },
            state,
        )

        result = {
            "citations": json.dumps(citations, ensure_ascii=False),
            "gap_checklist": json.dumps(gap_checklist, ensure_ascii=False),
            "deadline_calendar": json.dumps(deadline_calendar, ensure_ascii=False),
            "conflicts": json.dumps(conflicts, ensure_ascii=False),
            "disclaimer": disclaimer,
            "requires_human_review": final_review,
            "status": AgentStatus.SUCCESS.value,
        }
        if answer is None:
            result["error_code"] = LLM_NOT_CONFIGURED
            result["error_message"] = (
                "ComplianceOutputNode: no LLM bound (llm_client / config['llm']); "
                "statutes were retrieved but no answer was synthesized"
            )
        else:
            result["answer"] = answer
        return result


def _conflicts(per_jurisdiction: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report a conflict when JP and EU assign different risk classes."""
    by_j = {c.get("jurisdiction"): c.get("risk_class") for c in per_jurisdiction}
    jp, eu = by_j.get("JP"), by_j.get("EU")
    if jp and eu and jp != eu:
        return [
            {
                "type": "jurisdiction_divergence",
                "JP": jp,
                "EU": eu,
                "guidance": "JP and EU assign different risk classes; both are presented. "
                "Consult a compliance officer to reconcile — this template does "
                "not advise which jurisdiction's classification to adopt.",
            }
        ]
    return []
