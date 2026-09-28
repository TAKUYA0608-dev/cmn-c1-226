"""AuditLogNode (step 7b) — post_process slot / S-4 audit gate.

Emit one structured audit record per invocation: the classification *shape*
(risk classes, jurisdictions, confidence band, requires_human_review), citation /
gap / conflict counts, session_id, and the outcome. The AI-system identity and
any PII are NEVER logged — only counts and shape flags. Always fires (even on the
error path) — silent failure is prohibited.
"""

from __future__ import annotations

import json
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import CONFIDENCE_THRESHOLD
from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel


def _classification_shape(raw: Any) -> dict[str, Any]:
    """Redact classification to risk classes + confidence band (no raw system data)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        data = {}
    per_j = data.get("per_jurisdiction", []) if isinstance(data, dict) else []
    return {
        "jurisdictions": [c.get("jurisdiction") for c in per_j],
        "risk_classes": [c.get("risk_class") for c in per_j],
        "min_confidence_band": (
            "uncertain" if any((c.get("confidence", 1.0) < CONFIDENCE_THRESHOLD) for c in per_j) else "clear"
        )
        if per_j
        else None,
    }


class AuditLogNode(FunctionNode):
    """S-4: emit a redacted per-invocation audit event."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        def _count(field: str) -> int:
            try:
                v = json.loads(state.get(field) or "[]")
                return len(v) if isinstance(v, (list, dict)) else 0
            except (json.JSONDecodeError, TypeError):
                return 0

        payload: dict[str, Any] = {
            "jurisdiction": state.get("jurisdiction"),
            "question_length": len(state.get("validated_question") or state.get("question") or ""),
            "answer_length": len(state.get("answer") or ""),
            "citation_count": _count("citations"),
            "gap_count": _count("gap_checklist"),
            "conflict_count": _count("conflicts"),
            "retrieval_hit_count": state.get("retrieval_hit_count") or 0,
            "citation_status": state.get("citation_status"),
            "requires_human_review": bool(state.get("requires_human_review")),
            "classification_shape": _classification_shape(state.get("classification")),
        }
        if state.get("error_code"):
            payload["error_code"] = state["error_code"]

        emit_trace_event("agent_invoke_complete", payload, state)

        return {"audit_logged": True, "status": AgentStatus.SUCCESS.value}
