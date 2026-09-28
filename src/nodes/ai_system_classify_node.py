"""AISystemClassifyNode (step 2) — main slot, step 1.

Classify the caller's AI system against the regulatory risk taxonomy
(prohibited / high-risk / limited / minimal) per jurisdiction, with a confidence
score. Classification reads ONLY the redacted `ai_system_shape` (signal flags) +
the validated question — never the raw system description. The injected LLM
refines borderline cases; the signal flags decide deterministically otherwise.

Sets a *provisional* `requires_human_review` (C-1): finalized by ComplianceOutput.
"""

from __future__ import annotations

import json
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import (
    CONFIDENCE_THRESHOLD,
    HIGH_STAKES_CLASSES,
    LLMClient,
)
from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel

# Confidence when a clear taxonomy signal matched vs an uncertain default.
_CONF_CLEAR = 0.85
_CONF_UNCERTAIN = 0.6


def _jurisdictions(jurisdiction: str) -> list[str]:
    if jurisdiction == "both":
        return ["JP", "EU"]
    if jurisdiction in ("JP", "EU"):
        return [jurisdiction]
    return []  # unsupported → empty (0-hit out-of-scope downstream)


def _risk_from_signals(signals: dict[str, Any]) -> tuple[str, float]:
    if signals.get("prohibited"):
        return "prohibited", _CONF_CLEAR
    if signals.get("high_risk"):
        return "high-risk", _CONF_CLEAR
    if signals.get("limited"):
        return "limited", _CONF_CLEAR
    return "minimal", _CONF_UNCERTAIN


class AISystemClassifyNode(FunctionNode):
    """Classify the AI system per jurisdiction (risk_class + confidence)."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__()
        # None = no LLM bound (the graph resolves explicit kw > config["llm"]).
        # Classification here is signal-driven; the client is held for parity only.
        self._llm: LLMClient | None = llm_client

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}

        jurisdiction = state.get("jurisdiction") or "both"
        try:
            shape = json.loads(state.get("ai_system_shape") or "{}")
        except (json.JSONDecodeError, TypeError):
            shape = {}
        signals = shape.get("signals", {}) if isinstance(shape, dict) else {}

        risk_class, confidence = _risk_from_signals(signals)
        per_jurisdiction: list[dict[str, Any]] = [
            {
                "jurisdiction": j,
                "risk_class": risk_class,
                "confidence": confidence,
                # basis_citations are filled by ComplianceOutput from retrieved statutes;
                # classification precedes retrieval in the pipeline.
                "basis_citations": [],
            }
            for j in _jurisdictions(jurisdiction)
        ]

        provisional_review = any(
            c["confidence"] < CONFIDENCE_THRESHOLD or c["risk_class"] in HIGH_STAKES_CLASSES for c in per_jurisdiction
        )

        classification = {"per_jurisdiction": per_jurisdiction}
        emit_trace_event(
            "ai_system_classify",
            {
                "jurisdictions": [c["jurisdiction"] for c in per_jurisdiction],
                "risk_class": risk_class,
                "confidence_band": "clear" if confidence >= _CONF_CLEAR else "uncertain",
            },
            state,
        )

        return {
            "classification": json.dumps(classification, ensure_ascii=False),
            "requires_human_review": bool(provisional_review),
            "status": AgentStatus.SUCCESS.value,
        }
