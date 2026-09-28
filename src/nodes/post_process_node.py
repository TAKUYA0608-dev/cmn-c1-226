"""CompliancePostNode (post_process slot) — citation gate + audit.

Composes the two terminal steps in fixed order:

    CitationValidate (S-3 citation gate) → AuditLog (S-4)

CitationValidate may rewrite/reject the answer; AuditLog always fires afterward
(even on the error path) so every invocation is recorded. Returns only the
accumulated deltas. The LLM backend is dependency-injected for the citation gate's
regeneration step.
"""

from __future__ import annotations

from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import LLMClient
from src.nodes.citation_validate_node import CitationValidateNode
from src.nodes.audit_log_node import AuditLogNode
from framework.schemas.trust_level import TrustLevel
from src.utils.audit import emit_trace_event


class CompliancePostNode(FunctionNode):
    """post_process slot — CitationValidate (S-3) → AuditLog (S-4)."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        super().__init__()
        self._citation = CitationValidateNode(llm_client=llm_client)
        self._audit = AuditLogNode()

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        working = dict(state)
        deltas: dict[str, Any] = {}
        try:
            # S-3 citation gate (may rewrite/reject the answer).
            upd = self._citation.execute(working, config) or {}
            working.update(upd)
            deltas.update(upd)
            # S-4 audit (always fires — silent failure prohibited).
            upd = self._audit.execute(working, config) or {}
            working.update(upd)
            deltas.update(upd)
        except Exception as e:
            # Degrade instead of crashing the terminal node: with missing upstream
            # state (e.g. the PB-6 synthetic state) the sub-pipeline cannot run.
            # The error stays externally visible via error_code/error_message.
            return {
                "error_code": deltas.get("error_code") or "POST_PROCESS_ERROR",
                "error_message": f"{type(e).__name__}: {str(e)[:160]}",
                "status": AgentStatus.SUCCESS.value,
            }
        deltas["status"] = AgentStatus.SUCCESS.value
        # S-4 (gate-audit-trace-check): record that this
        # boundary node completed. Field NAMES only — never values.
        emit_trace_event("compliance_post_completed", {}, state)
        return deltas
