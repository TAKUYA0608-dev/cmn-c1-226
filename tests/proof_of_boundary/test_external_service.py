# PB-3 — L1 → External service boundary.
#
# The statute KB retriever is an injected external-service boundary. The agent
# must surface real retrieved data through the pipeline (not fabricate it), and
# the DI seam must be honoured (a missing backend degrades, never silently passes).

import json

from framework.schemas.invocation_context import InvocationContext, TrustLevel

from src.graph.graph import JapanAIRegulationComplianceQAAgent
from src.services.service import InMemoryRegulationBackend, StubLLMClient


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, session_id="pb3")


def _run(agent, q="biometric recruitment obligations", jurisdiction="both"):
    return agent.invoke(q, ctx=_ctx(),
                        input_context={"ai_system_description": json.dumps({"purpose": "biometric recruitment"}),
                                       "jurisdiction": jurisdiction})


def test_pb3_retrieved_data_flows_to_output():
    b = InMemoryRegulationBackend()
    b.add([{"statute_id": "AI-Act", "article": "Art.6", "title": "high risk", "text": "biometric recruitment high risk",
            "jurisdiction": "EU", "effective_date": "2026-08-02", "law_type": "obligation"}])
    a = JapanAIRegulationComplianceQAAgent(config={}, retrieval_backend=b,
                                           llm_client=StubLLMClient(canned="It is high-risk [AI-Act Art.6]."))
    a.compile()
    out = _run(a)
    assert out["retrieval_hit_count"] == 1
    recs = json.loads(out["citations"])
    assert recs[0]["statute_id"] == "AI-Act"  # real retrieved record surfaced


def test_pb3_no_backend_binds_seed_not_silent_none():
    # Contract change (default retrieval bind): no injected backend now binds the
    # illustrative seed KB — the DI seam still never silently passes a None
    # backend (a bare Graph() is the Marketplace runner / server.py form and must
    # be a working deployment form, not a permanent RETRIEVAL_BACKEND_MISSING).
    a = JapanAIRegulationComplianceQAAgent(config={}, retrieval_backend=None,
                                           llm_client=StubLLMClient(canned="x [AI-Act Art.6]"))
    a.compile()
    out = _run(a, q="high-risk ai systems obligations")
    assert out.get("error_code") != "RETRIEVAL_BACKEND_MISSING"
    assert out["retrieval_hit_count"] >= 1
    assert out["audit_logged"] is True


def test_pb3_failing_backend_degrades_not_silent():
    # The degradation property is preserved at the boundary: a backend that
    # raises surfaces an explicit error_code (never a fabricated answer).
    class _Boom:
        def search(self, query, top_k=8, jurisdiction=None):
            raise RuntimeError("down")

    a = JapanAIRegulationComplianceQAAgent(config={}, retrieval_backend=_Boom(),
                                           llm_client=StubLLMClient(canned="x [AI-Act Art.6]"))
    a.compile()
    out = _run(a)
    assert out["error_code"] == "RETRIEVAL_FAILED"
    assert out["audit_logged"] is True
