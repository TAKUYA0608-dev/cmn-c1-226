# CMN-C1-226 — Unit Tests: AISystemClassifyNode

import json

from src.nodes.ai_system_classify_node import AISystemClassifyNode


def _shape(**signals):
    return json.dumps({"keys": [], "signals": {"prohibited": False, "high_risk": False, "limited": False, **signals}})


def _run(jurisdiction="both", **signals):
    return AISystemClassifyNode().execute({"jurisdiction": jurisdiction, "ai_system_shape": _shape(**signals)})


def test_self_skips_on_error():
    assert AISystemClassifyNode().execute({"error_code": "X"}) == {}


def test_high_risk_signal():
    out = _run(high_risk=True)
    cls = json.loads(out["classification"])["per_jurisdiction"]
    assert {c["jurisdiction"] for c in cls} == {"JP", "EU"}
    assert all(c["risk_class"] == "high-risk" and c["confidence"] == 0.85 for c in cls)
    # high-risk is high-stakes ⇒ provisional human review
    assert out["requires_human_review"] is True


def test_prohibited_signal_requires_review():
    out = _run(prohibited=True)
    assert json.loads(out["classification"])["per_jurisdiction"][0]["risk_class"] == "prohibited"
    assert out["requires_human_review"] is True


def test_limited_signal_no_review():
    out = _run(limited=True)
    cls = json.loads(out["classification"])["per_jurisdiction"]
    assert all(c["risk_class"] == "limited" for c in cls)
    # limited + clear confidence (0.85) ⇒ no human review
    assert out["requires_human_review"] is False


def test_minimal_default_low_confidence_requires_review():
    out = _run()  # no signals
    cls = json.loads(out["classification"])["per_jurisdiction"]
    assert all(c["risk_class"] == "minimal" and c["confidence"] == 0.6 for c in cls)
    # confidence below τ=0.75 ⇒ human review
    assert out["requires_human_review"] is True


def test_single_jurisdiction():
    out = _run(jurisdiction="JP", limited=True)
    cls = json.loads(out["classification"])["per_jurisdiction"]
    assert [c["jurisdiction"] for c in cls] == ["JP"]


def test_unsupported_jurisdiction_yields_no_classification():
    out = _run(jurisdiction="US", high_risk=True)
    assert json.loads(out["classification"])["per_jurisdiction"] == []
