# PB-6 — Invoke execution order.
#
# node_history records visited node CLASS names. The citation gate + audit
# (CompliancePostNode) MUST run after the classify→...→output main slot and before
# finalize, or an uncited answer could escape unvalidated / unaudited.

import json

from framework.schemas.invocation_context import InvocationContext, TrustLevel

from src.graph.graph import JapanAIRegulationComplianceQAAgent
from src.services.service import InMemoryRegulationBackend, StubLLMClient

_CANONICAL = ["InitializeNode", "QueryNormalizeNode", "ComplianceMainNode",
              "CompliancePostNode", "FinalizeNode"]


def _agent():
    b = InMemoryRegulationBackend()
    b.add([{"statute_id": "AI-Act", "article": "Art.6", "title": "high risk", "text": "biometric recruitment high risk",
            "jurisdiction": "EU", "effective_date": "2026-08-02", "law_type": "obligation"}])
    a = JapanAIRegulationComplianceQAAgent(config={}, retrieval_backend=b,
                                           llm_client=StubLLMClient(canned="high-risk [AI-Act Art.6]"))
    a.compile()
    return a


def _history():
    out = _agent().invoke("biometric recruitment high risk",
                          ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
                          input_context={"ai_system_description": json.dumps({"purpose": "biometric"}), "jurisdiction": "both"})
    return out["node_history"]


def test_pb6_canonical_slot_order():
    history = _history()
    first = {n: history.index(n) for n in _CANONICAL}
    for a, b in zip(_CANONICAL, _CANONICAL[1:]):
        assert first[a] < first[b], f"PB-6: {a} must precede {b}, got {history}"


def test_pb6_initialize_first_finalize_last():
    history = _history()
    assert history[0] == "InitializeNode"
    assert history[-1] == "FinalizeNode"


def test_pb6_post_runs_after_main_before_finalize():
    history = _history()
    assert history.index("ComplianceMainNode") < history.index("CompliancePostNode")
    assert history.index("CompliancePostNode") < history.index("FinalizeNode")
