# CMN-C1-226 — Unit Tests: GapAnalyzeNode

import json

from src.nodes.gap_analyze_node import GapAnalyzeNode


def test_self_skips_on_error():
    assert GapAnalyzeNode().execute({"error_code": "X"}) == {}


def test_short_key_does_not_spuriously_mark_met():
    # ADV-1: a short field name ("or") must NOT substring-match inside "for"/"record"
    # and wrongly mark a hard-law obligation as met — it stays an (unknown) gap.
    state = {
        "ai_system_shape": json.dumps({"keys": ["or"], "signals": {}}),
        "obligations": json.dumps([
            {"jurisdiction": "EU", "text": "Maintain records for the conformity assessment",
             "basis_citation": "AI-Act Art.11", "law_type": "obligation"},
        ]),
    }
    gaps = json.loads(GapAnalyzeNode().execute(state)["gaps"])
    assert len(gaps) == 1 and gaps[0]["status"] == "unknown"


def test_unmet_obligation_becomes_gap_with_severity():
    state = {
        "ai_system_shape": json.dumps({"keys": ["purpose"], "signals": {}}),
        "obligations": json.dumps([
            {"jurisdiction": "EU", "text": "Maintain a risk management system", "basis_citation": "AI-Act Art.9", "law_type": "obligation"},
            {"jurisdiction": "JP", "text": "Follow METI best practice", "basis_citation": "METI §3", "law_type": "recommendation"},
        ]),
    }
    out = GapAnalyzeNode().execute(state)
    gaps = json.loads(out["gaps"])
    assert len(gaps) == 2
    hard = [g for g in gaps if g["basis_citation"] == "AI-Act Art.9"][0]
    assert hard["severity"] == "high" and hard["status"] == "unknown"
    soft = [g for g in gaps if g["basis_citation"] == "METI §3"][0]
    assert soft["severity"] == "medium"


def test_evidenced_obligation_not_a_gap():
    state = {
        "ai_system_shape": json.dumps({"keys": ["risk"], "signals": {}}),
        "obligations": json.dumps([
            {"jurisdiction": "EU", "text": "risk management documented", "basis_citation": "AI-Act Art.9", "law_type": "obligation"},
        ]),
    }
    out = GapAnalyzeNode().execute(state)
    assert json.loads(out["gaps"]) == []
