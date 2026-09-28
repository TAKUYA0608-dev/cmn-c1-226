"""ComplianceMainNode (main slot) — composes the 5 regulatory-reasoning steps.

The framework exposes three writable slots; the compliance core is five steps
composed into this single `main` node in fixed order:

    AISystemClassify → RegulationRetrieve → ObligationMap → GapAnalyze → ComplianceOutput

Each sub-node returns only its changed fields and self-skips on `error_code`.
This composite threads a running state to the sub-nodes but returns ONLY the
accumulated deltas (per the "return changed fields only" node contract).
Retrieval + LLM backends are dependency-injected into the sub-nodes that need them.
"""

from __future__ import annotations

from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import LLMClient, RegulationRetrievalBackend
from src.nodes.ai_system_classify_node import AISystemClassifyNode
from src.nodes.regulation_retrieve_node import RegulationRetrieveNode
from src.nodes.obligation_map_node import ObligationMapNode
from src.nodes.gap_analyze_node import GapAnalyzeNode
from src.nodes.compliance_output_node import ComplianceOutputNode
from framework.schemas.trust_level import TrustLevel
from src.utils.audit import emit_trace_event


class ComplianceMainNode(FunctionNode):
    """main slot — Classify → Retrieve → ObligationMap → GapAnalyze → ComplianceOutput."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(
        self,
        retrieval_backend: RegulationRetrievalBackend | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        super().__init__()
        self._seq: list[FunctionNode] = [
            AISystemClassifyNode(llm_client=llm_client),
            RegulationRetrieveNode(retrieval_backend=retrieval_backend),
            ObligationMapNode(),
            GapAnalyzeNode(),
            ComplianceOutputNode(llm_client=llm_client),
        ]

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            # Upstream pre_process rejected the input: skip the composite
            # sub-pipeline. The framework router still advances to post_process,
            # whose terminal S-4 audit always runs.
            return {"status": AgentStatus.SUCCESS.value}
        working = dict(state)
        deltas: dict[str, Any] = {}
        try:
            for node in self._seq:
                updates = node.execute(working, config) or {}
                working.update(updates)
                deltas.update(updates)
        except Exception as e:
            # Degrade instead of crashing the node: with missing upstream state
            # (e.g. the PB-6 synthetic state) or unavailable dependencies the
            # sub-pipeline cannot run. post_process still runs the terminal
            # S-4 audit and surfaces the error_code.
            return {
                "error_code": "MAIN_PIPELINE_ERROR",
                "error_message": f"{type(e).__name__}: {str(e)[:160]}",
                "status": AgentStatus.SUCCESS.value,
            }
        # Always report SUCCESS — even when a sub-node set error_code — so the
        # framework routes on to post_process instead of aborting. We never raise or
        # short-circuit on a domain error here: the error_code rides along in deltas
        # and is surfaced by CitationValidate (gate pass-through) + AuditLog (which
        # records error_code) at the terminus. This keeps every invocation auditable.
        deltas["status"] = AgentStatus.SUCCESS.value
        # S-4 (gate-audit-trace-check): record that this
        # boundary node completed. Field NAMES only — never values.
        emit_trace_event("compliance_main_completed", {}, state)
        return deltas
