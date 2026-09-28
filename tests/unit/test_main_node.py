# CMN-C1-226 — Unit Tests: ComplianceMainNode (composite, delta-return)

import inspect
import json

from src.nodes.main_node import ComplianceMainNode
from src.services.service import InMemoryRegulationBackend, StubLLMClient


def _backend():
    b = InMemoryRegulationBackend()
    b.add([
        {"statute_id": "AI-Act", "article": "Art.6", "title": "high risk", "text": "biometric recruitment high risk",
         "jurisdiction": "EU", "effective_date": "2026-08-02", "law_type": "obligation"},
        {"statute_id": "Japan-AI-Reg-2026", "article": "Art.5", "title": "operator duties", "text": "biometric duties",
         "jurisdiction": "JP", "effective_date": "2026-06-01", "law_type": "obligation"},
    ])
    return b


def _node():
    return ComplianceMainNode(retrieval_backend=_backend(),
                              llm_client=StubLLMClient(canned="EU high-risk [AI-Act Art.6]. JP duties [Japan-AI-Reg-2026 Art.5]."))


class TestComplianceMainNode:
    def test_runs_all_five_steps(self):
        state = {
            "validated_question": "is biometric recruitment high risk",
            "jurisdiction": "both",
            "ai_system_shape": json.dumps({"keys": ["purpose"], "signals": {"high_risk": True}}),
        }
        out = _node().execute(state)
        assert json.loads(out["classification"])["per_jurisdiction"]
        assert out["retrieval_hit_count"] == 2
        assert json.loads(out["obligations"])
        assert "gaps" in out
        assert "[AI-Act Art.6]" in out["answer"]
        assert out["status"] == "success"

    def test_self_skips_on_upstream_error(self):
        out = _node().execute({"error_code": "INPUT_EMPTY", "jurisdiction": "both"})
        assert out["status"] == "success"
        assert "answer" not in out

    def test_returns_only_deltas(self):
        state = {"validated_question": "q", "jurisdiction": "both",
                 "ai_system_shape": json.dumps({"keys": [], "signals": {"high_risk": True}})}
        out = _node().execute(state)
        assert "validated_question" not in out

    def test_execute_signature_is_node_contract(self):
        # node contract: override execute(self, state, ...), not _invoke_impl
        params = list(inspect.signature(ComplianceMainNode.execute).parameters.keys())
        assert params[1] == "state"
        assert "_invoke_impl" not in ComplianceMainNode.__dict__
