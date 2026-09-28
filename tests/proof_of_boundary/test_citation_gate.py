# PB (domain) — S-3 citation gate end-to-end.
#
# The core safety property: no uncited regulatory claim escapes the agent. An LLM
# that keeps producing uncited compliance assertions must end in a rejected,
# safe answer — proven through the full invoke(), not just the node in isolation.

import json

from framework.schemas.invocation_context import InvocationContext, TrustLevel

from src.graph.graph import JapanAIRegulationComplianceQAAgent
from src.services.service import InMemoryRegulationBackend, StubLLMClient


def _agent(canned):
    b = InMemoryRegulationBackend()
    b.add([{"statute_id": "AI-Act", "article": "Art.6", "title": "high risk", "text": "biometric recruitment high risk",
            "jurisdiction": "EU", "effective_date": "2026-08-02", "law_type": "obligation"}])
    a = JapanAIRegulationComplianceQAAgent(config={}, retrieval_backend=b, llm_client=StubLLMClient(canned=canned))
    a.compile()
    return a


def _run(agent):
    return agent.invoke("Is our biometric recruitment system high-risk?",
                        ctx=InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, session_id="pbc"),
                        input_context={"ai_system_description": json.dumps({"purpose": "biometric recruitment"}),
                                       "jurisdiction": "both"})


def test_cited_answer_passes():
    out = _run(_agent("The system is high-risk and must meet documentation duties [AI-Act Art.6]."))
    assert out["citation_status"] == "passed"
    assert "[AI-Act Art.6]" in out["answer"]


def test_persistently_uncited_answer_is_rejected():
    out = _run(_agent("This system is definitely high risk and you must comply with everything immediately, no references."))
    assert out["citation_status"] == "rejected"
    assert "withholding" in out["answer"].lower()
    # rejection still produces an audit record
    assert out["audit_logged"] is True
