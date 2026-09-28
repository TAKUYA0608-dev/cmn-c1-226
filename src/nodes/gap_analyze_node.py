"""GapAnalyzeNode (step 5) — main slot, step 4.

Analyse the AI system (its redacted shape) against the obligation set → a gap
list. Each gap records the obligation, a met/unmet/unknown status, and a
severity. The system shape carries presence flags only, so an obligation whose
evidence field is absent in the shape is flagged `unknown` (needs confirmation),
never silently assumed met.
"""

from __future__ import annotations

import json
import re
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel

# Obligation law_type → gap severity when unmet/unknown (hard law is most severe).
_SEVERITY = {"obligation": "high", "recommendation": "medium", "interpretation": "low"}


class GapAnalyzeNode(FunctionNode):
    """Compare obligations against the system shape → gap list."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}

        try:
            obligations = json.loads(state.get("obligations") or "[]")
        except (json.JSONDecodeError, TypeError):
            obligations = []
        try:
            shape = json.loads(state.get("ai_system_shape") or "{}")
        except (json.JSONDecodeError, TypeError):
            shape = {}
        present_keys = {str(k).lower() for k in shape.get("keys", [])} if isinstance(shape, dict) else set()

        gaps: list[dict[str, Any]] = []
        for ob in obligations:
            text = str(ob.get("text", "")).lower()
            # Heuristic: if the obligation references a concept absent from the
            # system shape's declared fields, it is an unknown/unmet gap. Match on
            # word boundaries (not substring) so a short field name like "or" does
            # not spuriously mark an obligation "met".
            evidenced = any(k and re.search(rf"\b{re.escape(k)}\b", text) for k in present_keys)
            status = "met" if evidenced else "unknown"
            if status != "met":
                gaps.append(
                    {
                        "obligation": ob.get("text", ""),
                        "basis_citation": ob.get("basis_citation", ""),
                        "jurisdiction": ob.get("jurisdiction"),
                        "status": status,
                        "severity": _SEVERITY.get(ob.get("law_type", "obligation"), "high"),
                    }
                )

        emit_trace_event(
            "gap_analyze",
            {"gap_count": len(gaps), "obligation_count": len(obligations)},
            state,
        )

        return {
            "gaps": json.dumps(gaps, ensure_ascii=False),
            "status": AgentStatus.SUCCESS.value,
        }
