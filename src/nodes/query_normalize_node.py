"""QueryNormalizeNode (step 1) — pre_process slot / S-1 input boundary.

Validate the NL question + jurisdiction, redact the caller's AI-system
description to a *shape* (presence flags + risk-signal categories only — never
the raw identity / PII), and reject prompt-injection before any LLM sees input.

Node contract: extend FunctionNode; override
`execute(self, state, config=None) -> dict`; return ONLY changed fields + an
AgentStatus enum; read input_context read-only.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.utils.audit import emit_trace_event

_MAX_LEN = 4000

# Prompt-injection / jailbreak signatures (reject, never forward to the LLM).
_INJECTION = re.compile(
    r"(?i)(ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions"
    r"|disregard\s+(?:the\s+)?(?:system|previous)\s+(?:prompt|instructions)"
    r"|reveal\s+(?:your\s+)?system\s+prompt"
    r"|you\s+are\s+now\s+(?:a|an|in)\b"
    r"|<\s*script\b|</\s*script\s*>)"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Risk-signal categories (regulatory taxonomy). Stored as booleans in the shape
# (never the raw text), and consumed by AISystemClassifyNode.
_PROHIBITED_SIGNALS = re.compile(
    r"(?i)(social[\s-]?scoring|subliminal|manipulat|real[\s-]?time\s+remote\s+biometric"
    r"|mass\s+surveillance|predictive\s+policing\s+profil)"
)
_HIGH_RISK_SIGNALS = re.compile(
    r"(?i)(biometric|facial\s+recognition|recruit|hiring|resume\s+screen|cv\s+screen"
    r"|credit\s+scor|creditworthi|critical\s+infrastructure|medical|diagnos"
    r"|law\s+enforcement|exam\s+scor|education\s+grad|emotion\s+recognition)"
)
_LIMITED_SIGNALS = re.compile(r"(?i)(chatbot|conversational|deepfake|synthetic\s+media|content\s+generat|transparency)")


def _normalize_jurisdiction(value: Any) -> str:
    """Map a caller jurisdiction to JP / EU / both; unknown values pass through
    (they 0-hit downstream → out-of-scope). Missing → 'both'."""
    if not value:
        return "both"
    v = str(value).strip().lower()
    if v in ("jp", "japan", "日本"):
        return "JP"
    if v in ("eu", "europe", "european union"):
        return "EU"
    if v in ("both", "jp+eu", "eu+jp", "jp/eu", "all"):
        return "both"
    return str(value).strip().upper()  # unsupported → 0-hit out-of-scope


def _shape(description: Any, scan_text: str) -> str:
    """Redact the AI-system description to presence flags + risk signals only."""
    data: dict[str, Any] = {}
    if isinstance(description, str) and description.strip():
        try:
            parsed = json.loads(description)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, TypeError):
            data = {}
    elif isinstance(description, dict):
        data = description

    shape = {
        "has_description": bool(description),
        "keys": sorted(data.keys()),  # field NAMES only (schema, not PII)
        "signals": {
            "prohibited": bool(_PROHIBITED_SIGNALS.search(scan_text)),
            "high_risk": bool(_HIGH_RISK_SIGNALS.search(scan_text)),
            "limited": bool(_LIMITED_SIGNALS.search(scan_text)),
        },
    }
    return json.dumps(shape, ensure_ascii=False)


class QueryNormalizeNode(FunctionNode):
    """S-1: validate question + jurisdiction; redact system description; reject injection."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}

        ic = state.get("input_context") or {}
        raw = state.get("question") or state.get("user_input") or ""
        description = state.get("ai_system_description") or ic.get("ai_system_description")
        jurisdiction_in = state.get("jurisdiction") or ic.get("jurisdiction")

        if not raw or not str(raw).strip():
            return {
                "error_code": "INPUT_EMPTY",
                "error_message": "QueryNormalizeNode: question is empty or missing",
                "status": AgentStatus.ERROR.value,
            }
        if len(str(raw)) > _MAX_LEN:
            return {
                "error_code": "INPUT_TOO_LONG",
                "error_message": f"QueryNormalizeNode: question exceeds {_MAX_LEN} chars",
                "status": AgentStatus.ERROR.value,
            }
        # Injection scan covers the question AND the system description text.
        desc_text = description if isinstance(description, str) else json.dumps(description or {}, ensure_ascii=False)
        if _INJECTION.search(str(raw)) or _INJECTION.search(desc_text):
            return {
                "error_code": "INJECTION_DETECTED",
                "error_message": "QueryNormalizeNode: prompt-injection pattern rejected",
                "status": AgentStatus.ERROR.value,
            }

        validated = unicodedata.normalize("NFKC", str(raw))
        validated = _CONTROL.sub("", validated).strip()
        validated = re.sub(r"\s+", " ", validated)

        scan_text = f"{validated}\n{desc_text}"
        # S-4 (gate-audit-trace-check): record that this
        # boundary node completed. Field NAMES only — never values.
        emit_trace_event(
            "query_normalize_completed",
            {"fields": ["ai_system_shape", "jurisdiction", "question", "status", "validated_question"]},
            state,
        )
        return {
            "question": validated,
            "validated_question": validated,
            "jurisdiction": _normalize_jurisdiction(jurisdiction_in),
            "ai_system_shape": _shape(description, scan_text),
            "status": AgentStatus.SUCCESS.value,
        }
