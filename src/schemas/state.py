"""CMN-C1-226 Japan AI Regulation 2026 Compliance Q&A Agent — State schema.

Flat TypedDict per the platform's node and state-safety contracts:
all fields are Optional primitives or JSON-serialized strings (msgpack
round-trips cleanly). No Pydantic, no dataclass, no arbitrary Python objects,
and never JWT / API keys / credentials (checkpoint DB leakage). The caller's
AI-system description is NEVER stored raw — only its *shape* (presence flags)
survives, so company identity / PII never reaches the checkpoint or audit log.

Pipeline (docs/02_design.md §2):
    QueryNormalize → AISystemClassify → RegulationRetrieve → ObligationMap
    → GapAnalyze → ComplianceOutput → CitationValidate (S-3) → AuditLog (S-4)
"""

from __future__ import annotations

from typing import Optional

from framework.schemas.agent_state import AgentState


class ComplianceQAState(AgentState):
    """State for the regulatory-compliance Q&A pipeline.

    Shared fields (user_input, status, session_id, node_history, error_log,
    caller_trust_level, trace_id, correlation_id, …) are inherited from
    AgentState. Only domain fields are declared here; LangGraph drops keys not in
    this schema, so every field the pipeline writes MUST appear below.
    """

    # ─── Input (caller supplies via user_input + input_context) ───
    question: Optional[str]  # NL compliance question / classification request
    ai_system_description: Optional[str]  # JSON: caller's system spec (read-only; redacted at pre_process)
    jurisdiction: Optional[str]  # "JP" | "EU" | "both"

    # ─── QueryNormalizeNode (S-1 input boundary) ───
    validated_question: Optional[str]  # sanitized / normalized question
    ai_system_shape: Optional[str]  # JSON: presence flags only (no identity / PII)

    # ─── AISystemClassifyNode ───
    classification: Optional[str]  # JSON: {per_jurisdiction:[{jurisdiction,risk_class,confidence,basis_citations}]}
    requires_human_review: Optional[bool]  # provisional here; finalized by ComplianceOutput (C-1 policy)

    # ─── RegulationRetrieveNode (jurisdiction-aware RAG over statute KB) ───
    retrieved_statutes: Optional[
        str
    ]  # JSON: [{statute_id, article, title, text, score, jurisdiction, effective_date, kb_version, law_type}]
    retrieval_hit_count: Optional[int]  # total statute records retrieved

    # ─── ObligationMapNode ───
    obligations: Optional[str]  # JSON: [{jurisdiction, text, basis_citation, law_type}]

    # ─── GapAnalyzeNode ───
    gaps: Optional[str]  # JSON: [{obligation, status, severity}]

    # ─── ComplianceOutputNode ───
    answer: Optional[str]  # cited answer (inline [<statute> <article>] markers)
    citations: Optional[str]  # JSON list: [{marker, statute_id, article, jurisdiction}]
    gap_checklist: Optional[str]  # JSON list (actionable items)
    deadline_calendar: Optional[str]  # JSON list ([{obligation, effective_date, jurisdiction}])
    conflicts: Optional[str]  # JSON list (JP↔EU divergences surfaced, not collapsed)
    disclaimer: Optional[str]  # mandatory "not legal advice" notice (D)

    # ─── CitationValidateNode (S-3 citation gate) ───
    citation_status: Optional[str]  # "passed" | "regenerated" | "rejected"
    uncited_claims: Optional[str]  # JSON list of claims lacking a statute/article reference

    # ─── AuditLogNode (S-4) ───
    audit_logged: Optional[bool]  # True once the audit event is emitted

    # ─── Error propagation (any node; downstream nodes self-skip) ───
    error_code: Optional[str]
    error_message: Optional[str]


# Backward-compat alias: the scaffold (graph.py / server.py) references `State`.
State = ComplianceQAState
