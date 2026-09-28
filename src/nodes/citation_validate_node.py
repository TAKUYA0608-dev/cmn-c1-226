"""CitationValidateNode (step 7a) — post_process slot. **The S-3 citation gate.**

The quality core: every substantive regulatory claim in the answer MUST cite a
statute/article (e.g. [Japan-AI-Reg-2026 Art.5], [AI-Act Art.6], [APPI-2026 第20条]).
Uncited claims trigger one regeneration with a "cite every claim" instruction; if
claims are still uncited, the answer is rejected (replaced with a safe response).
This is domain validation (LLM regeneration) in `execute()` — distinct from the
framework `@final` credential gate.

citation_status: passed | regenerated | rejected.
"""

from __future__ import annotations

import json
import re
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import LLM_NOT_CONFIGURED, LLMClient
from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel

# A statute/article citation: a bracketed reference containing an article-ish token
# (Art./Article/Annex, or a Japanese 第N条). Matches EU + JP + APPI citation styles.
_CITATION = re.compile(
    r"\[[^\]]*(?:Art\.?\s*\d|Article\s*\d|Annex|第\s*[0-9〇一二三四五六七八九十百千]+\s*条)[^\]]*\]",
    re.IGNORECASE,
)
_MIN_CLAIM_WORDS = 5
# Japanese sentences don't split on whitespace (len(s.split()) ≈ 1), so a claim is
# also treated as "substantive" once it reaches this character length — otherwise a
# long uncited JP claim would slip past the word-count check.
_MIN_CLAIM_CHARS = 16

_REJECTED = (
    "I can't ground every regulatory claim in a statute/article, so I'm withholding "
    "this answer to avoid unsupported compliance assertions. Please rephrase toward a "
    "statute-covered topic or consult a compliance officer. (This is not legal advice.)"
)


def _uncited_claims(answer: str) -> list[str]:
    """Return substantive answer sentences that lack a statute/article citation."""
    if not answer:
        return []
    # Split on ASCII enders (require trailing whitespace, so "Art.6" is not split)
    # AND on JP enders 。！？ (which are typically NOT followed by whitespace), so
    # packed Japanese sentences are separated too.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*", answer) if s.strip()]
    out = []
    for s in sentences:
        substantive = len(s.split()) >= _MIN_CLAIM_WORDS or len(s) >= _MIN_CLAIM_CHARS
        if substantive and not _CITATION.search(s):
            out.append(s)
    return out


class CitationValidateNode(FunctionNode):
    """Enforce that every answer claim cites a statute/article."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__()
        # None = no LLM bound. Resolved by the graph (explicit kw > config["llm"]);
        # a bare node cannot regenerate and rejects an uncited answer outright.
        self._llm: LLMClient | None = llm_client

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        answer = state.get("answer") or ""
        # Nothing to gate (upstream error, empty answer, or a 0-hit out-of-scope
        # refusal — a refusal is a meta-statement, not a grounded statutory claim)
        # → pass through.
        if not answer or state.get("error_code") or (state.get("retrieval_hit_count") or 0) <= 0:
            return {
                "citation_status": "passed",
                "uncited_claims": json.dumps([], ensure_ascii=False),
                "status": AgentStatus.SUCCESS.value,
            }

        uncited = _uncited_claims(answer)
        if not uncited:
            return {
                "citation_status": "passed",
                "uncited_claims": json.dumps([], ensure_ascii=False),
                "status": AgentStatus.SUCCESS.value,
            }

        if self._llm is None:
            # No LLM to regenerate with: fail closed on the uncited answer.
            emit_trace_event(
                "llm_not_configured",
                {"reason_code": LLM_NOT_CONFIGURED, "uncited_count": len(uncited)},
                state,
            )
            emit_trace_event("citation_gate_reject", {"uncited_count": len(uncited)}, state)
            return {
                "answer": _REJECTED,
                "citation_status": "rejected",
                "uncited_claims": json.dumps(uncited, ensure_ascii=False),
                "status": AgentStatus.SUCCESS.value,
            }

        # Regenerate once, demanding a statute/article citation on every claim.
        correction = (
            "Your previous answer contained regulatory claims with no statute citation. "
            "Rewrite it so EVERY sentence that states an obligation, classification, or "
            "deadline ends with an inline [<statute_id> <article>] citation. Do not add "
            "unsupported claims.\n\n=== Previous answer ===\n" + answer
        )
        regenerated = self._llm.generate(correction)
        emit_trace_event("citation_gate_regenerate", {"uncited_count": len(uncited)}, state)

        residual = _uncited_claims(regenerated)
        if residual:
            emit_trace_event("citation_gate_reject", {"uncited_count": len(residual)}, state)
            return {
                "answer": _REJECTED,
                "citation_status": "rejected",
                "uncited_claims": json.dumps(uncited, ensure_ascii=False),
                "status": AgentStatus.SUCCESS.value,
            }

        return {
            "answer": regenerated,
            "citation_status": "regenerated",
            "uncited_claims": json.dumps(uncited, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
