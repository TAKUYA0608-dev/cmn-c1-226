"""RegulationRetrieveNode (step 3) — main slot, step 2.

Jurisdiction-aware RAG over the statute KB via the injected backend. Statute
facts (article numbers, effective dates) are KB *data* (docs/02_design.md §1.2
A-1) — this node never hard-codes them. External-service boundary (PB-3); emits
an S-4 audit event (counts only).
"""

from __future__ import annotations

import json
from typing import Any

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus

from src.services.service import RegulationRetrievalBackend, seed_regulation_kb
from src.utils.audit import emit_trace_event
from framework.schemas.trust_level import TrustLevel

_TOP_K = 8


class RegulationRetrieveNode(FunctionNode):
    """Retrieve governing statutes/articles (dependency-injected backend)."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, retrieval_backend: RegulationRetrievalBackend | None = None, top_k: int = _TOP_K) -> None:
        super().__init__()
        # Default = illustrative in-memory seed KB (D-form fleet idiom). A bare
        # Graph() is what both server.py and the Marketplace runner construct, so
        # the default must be a working deployment form — with no backend every
        # run degraded to RETRIEVAL_BACKEND_MISSING. Deployments with a real
        # statute store still inject their own backend here (unchanged).
        self._backend: RegulationRetrievalBackend = retrieval_backend or seed_regulation_kb()
        self._top_k = top_k

    def execute(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state.get("error_code"):
            return {}
        if self._backend is None:
            return {
                "error_code": "RETRIEVAL_BACKEND_MISSING",
                "error_message": "RegulationRetrieveNode: no RegulationRetrievalBackend injected",
                "status": AgentStatus.ERROR.value,
            }

        query = state.get("validated_question") or state.get("question") or ""
        jurisdiction = state.get("jurisdiction") or "both"

        try:
            statutes = self._backend.search(query, top_k=self._top_k, jurisdiction=jurisdiction) or []
        except Exception as exc:  # graceful degradation per design §9
            return {
                "error_code": "RETRIEVAL_FAILED",
                "error_message": f"RegulationRetrieveNode: backend error: {exc}",
                "status": AgentStatus.ERROR.value,
            }

        emit_trace_event(
            "regulation_retrieve",
            {"hit_count": len(statutes), "top_k": self._top_k, "jurisdiction": jurisdiction},
            state,
        )

        return {
            "retrieved_statutes": json.dumps(statutes, ensure_ascii=False),
            "retrieval_hit_count": len(statutes),
            "status": AgentStatus.SUCCESS.value,
        }
