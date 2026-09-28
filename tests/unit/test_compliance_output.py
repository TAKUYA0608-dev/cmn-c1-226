# CMN-C1-226 — Unit Tests: ComplianceOutputNode (final synthesis)

import json

from src.nodes.compliance_output_node import ComplianceOutputNode
from src.services.service import StubLLMClient


def _statutes(jp_class="high-risk", eu_class="high-risk"):
    return json.dumps([
        {"statute_id": "AI-Act", "article": "Art.6", "title": "EU classification", "text": "high risk",
         "jurisdiction": "EU", "effective_date": "2026-08-02", "law_type": "obligation"},
        {"statute_id": "Japan-AI-Reg-2026", "article": "Art.5", "title": "JP operator duties", "text": "duties",
         "jurisdiction": "JP", "effective_date": "2026-06-01", "law_type": "obligation"},
    ])


def _state(jp_class="high-risk", eu_class="high-risk", hits=2):
    return {
        "validated_question": "is this high risk",
        "retrieval_hit_count": hits,
        "retrieved_statutes": _statutes() if hits else json.dumps([]),
        "classification": json.dumps({"per_jurisdiction": [
            {"jurisdiction": "JP", "risk_class": jp_class, "confidence": 0.85},
            {"jurisdiction": "EU", "risk_class": eu_class, "confidence": 0.85},
        ]}),
        "gaps": json.dumps([{"obligation": "risk mgmt", "basis_citation": "AI-Act Art.9",
                             "jurisdiction": "EU", "severity": "high", "status": "unknown"}]),
        "requires_human_review": True,
    }


def _node(canned="Under EU high-risk [AI-Act Art.6]. JP duties apply [Japan-AI-Reg-2026 Art.5]."):
    return ComplianceOutputNode(llm_client=StubLLMClient(canned=canned))


def test_self_skips_on_error():
    assert _node().execute({"error_code": "X"}) == {}


def test_zero_hits_out_of_scope():
    out = _node().execute(_state(hits=0))
    assert "outside the current regulatory knowledge base" in out["answer"]
    assert json.loads(out["citations"]) == []
    assert out["disclaimer"]  # disclaimer always present


def test_cited_answer_and_artifacts():
    out = _node().execute(_state())
    assert "[AI-Act Art.6]" in out["answer"]
    assert len(json.loads(out["citations"])) == 2
    assert len(json.loads(out["gap_checklist"])) == 1
    cal = json.loads(out["deadline_calendar"])
    assert {c["effective_date"] for c in cal} == {"2026-08-02", "2026-06-01"}


def test_disclaimer_has_human_review_note_when_flagged():
    out = _node().execute(_state())
    assert "not legal advice" in out["disclaimer"].lower()
    assert "human review" in out["disclaimer"].lower()
    assert out["requires_human_review"] is True


def test_conflict_surfaced_not_collapsed():
    out = _node().execute(_state(jp_class="limited", eu_class="high-risk"))
    conflicts = json.loads(out["conflicts"])
    assert len(conflicts) == 1
    assert conflicts[0]["JP"] == "limited" and conflicts[0]["EU"] == "high-risk"
    # never tells the caller which side to adopt
    assert "does not advise" in conflicts[0]["guidance"]


def test_minimal_clear_no_human_review():
    s = _state(jp_class="limited", eu_class="limited")
    s["requires_human_review"] = False
    out = _node().execute(s)
    assert out["requires_human_review"] is False
    assert "human review" not in out["disclaimer"].lower()
