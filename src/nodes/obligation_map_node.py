"""ObligationMapNode (step 4) — main slot, step 3.

Map the per-jurisdiction classification to its obligation set, grounded in the
retrieved statutes. Each obligation carries a `basis_citation` (statute/article)
and a `law_type` (obligation | recommendation | interpretation) so hard law,
METI soft law, and SIer interpretation are never conflated (docs/02_design.md A-4).
"""

from __future__ import annotations

import json
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel


def _citation(rec: dict[str, Any]) -> str:
    sid = rec.get("statute_id", "")
    art = rec.get("article", "")
    return f"{sid} {art}".strip()


class ObligationMapNode(FunctionNode):
    """Derive the obligation set from classification + retrieved statutes."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}

        try:
            statutes = json.loads(state.get("retrieved_statutes") or "[]")
        except (json.JSONDecodeError, TypeError):
            statutes = []
        try:
            classification = json.loads(state.get("classification") or "{}")
        except (json.JSONDecodeError, TypeError):
            classification = {}

        per_j = classification.get("per_jurisdiction", []) if isinstance(classification, dict) else []
        active_jurisdictions = {c.get("jurisdiction") for c in per_j}

        obligations: list[dict[str, Any]] = []
        for rec in statutes:
            j = rec.get("jurisdiction")
            # "both"-tagged KB records apply to every active jurisdiction.
            if active_jurisdictions and j not in active_jurisdictions and j != "both":
                continue
            obligations.append(
                {
                    "jurisdiction": j,
                    "text": rec.get("title") or rec.get("text", "")[:160],
                    "basis_citation": _citation(rec),
                    "law_type": rec.get("law_type", "obligation"),
                }
            )

        emit_trace_event(
            "obligation_map",
            {"obligation_count": len(obligations), "jurisdictions": sorted(j for j in active_jurisdictions if j)},
            state,
        )

        return {
            "obligations": json.dumps(obligations, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
