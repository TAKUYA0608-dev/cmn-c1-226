"""CMN-C1-226 JapanAIRegulationComplianceQAAgent — graph composition.

L1-direct inheritance from AgentBaseGraph. The 7 domain steps are composed into
the framework's three writable slots; the framework owns initialize/finalize,
edge wiring, and routing.

    pre_process  = QueryNormalizeNode   (S-1 input boundary)
    main         = ComplianceMainNode   (Classify→Retrieve→ObligationMap→GapAnalyze→ComplianceOutput)
    post_process = CompliancePostNode   (CitationValidate S-3 gate → AuditLog S-4)

Retrieval + LLM backends are dependency-injected so the agent is offline-testable
and deployment-agnostic. Production binds the real statute vector store; tests bind
in-memory / stub backends.

LLM binding: explicit `llm_client` kw > `config["llm"]` (the Marketplace entry point
places a lazy Azure client there; adapted by `resolve_llm_client`) > none. With none,
a run that retrieved statutes degrades with `LLM_NOT_CONFIGURED` (SUCCESS + error_code
+ non-empty notice; the deterministic citations / checklist / calendar are kept) — no
stub is bound and no prose is invented.
"""

from __future__ import annotations
from typing import Any

from framework.graph.agent_base_graph import AgentBaseGraph

from src.schemas.state import ComplianceQAState
from src.services.service import LLM_NOT_CONFIGURED, LLMClient, RegulationRetrievalBackend, resolve_llm_client
from src.nodes.query_normalize_node import QueryNormalizeNode
from src.nodes.main_node import ComplianceMainNode
from src.nodes.post_process_node import CompliancePostNode


class JapanAIRegulationComplianceQAAgent(AgentBaseGraph):
    """Japan/EU AI-regulation compliance Q&A agent (Cat 1, flagship)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        retrieval_backend: RegulationRetrievalBackend | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._retrieval_backend = retrieval_backend
        self._llm_client = resolve_llm_client(llm_client, config)
        super().__init__(config)

    @property
    def name(self) -> str:
        return "JapanAIRegulationComplianceQAAgent"

    @property
    def state_schema(self) -> type:
        """Domain State so per-node fields survive node merges (LangGraph drops
        keys not declared in the schema)."""
        return ComplianceQAState

    def register_nodes(self) -> None:
        super().register_nodes()  # framework injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = QueryNormalizeNode()
        self._nodes["main"] = ComplianceMainNode(
            retrieval_backend=self._retrieval_backend,
            llm_client=self._llm_client,
        )
        self._nodes["post_process"] = CompliancePostNode(llm_client=self._llm_client)

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        """Surface the compliance payload (this agent writes answer/classification/
        obligations/etc., not the framework-default formatted_output)."""
        # The Marketplace runner rejects a successful invocation whose output
        # is missing (verified on a deployed Pod), and a degraded run
        # (SUCCESS + error_code) leaves "answer" unset. Report the degradation —
        # this states what happened, it does not invent an answer.
        #
        # Only on SUCCESS: a request refused by the S-2 gate (status ERROR) must
        # keep publishing nothing, or the refusal is undone.
        _output = state.get("answer")
        if not _output and str(state.get("status", "")).lower().endswith("success"):
            _code = state.get("error_code") or "NO_CONTENT"
            if _code == LLM_NOT_CONFIGURED:
                # Statutes were retrieved; nothing synthesized the prose answer.
                # Name the cause and what IS in the envelope — a notice, not an answer.
                _output = (
                    f"This request could not be completed (error_code={LLM_NOT_CONFIGURED}). "
                    "No language model is bound to this deployment, so no answer was "
                    "synthesized from the retrieved statutes "
                    f"(statutes retrieved: {state.get('retrieval_hit_count') or 0}). "
                    "The deterministic citations, gap checklist, deadline calendar, "
                    "conflicts and disclaimer are provided in the envelope fields."
                )
            else:
                _output = (
                    "This request could not be completed "
                    f"(error_code={_code}). No content was produced; "
                    "see error_code and error_log for the degradation cause."
                )
        return {
            "output": _output,
            "answer": state.get("answer"),
            "classification": state.get("classification"),
            "requires_human_review": state.get("requires_human_review"),
            "citations": state.get("citations"),
            "obligations": state.get("obligations"),
            "gaps": state.get("gaps"),
            "gap_checklist": state.get("gap_checklist"),
            "deadline_calendar": state.get("deadline_calendar"),
            "conflicts": state.get("conflicts"),
            "disclaimer": state.get("disclaimer"),
            "jurisdiction": state.get("jurisdiction"),
            "retrieval_hit_count": state.get("retrieval_hit_count"),
            "citation_status": state.get("citation_status"),
            "uncited_claims": state.get("uncited_claims"),
            "audit_logged": state.get("audit_logged"),
            "status": state.get("status"),
            "error_code": state.get("error_code"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "error_log": state.get("error_log", []),
        }


# Backward-compat alias — the scaffold (api/server.py) imports `Graph`.
Graph = JapanAIRegulationComplianceQAAgent
