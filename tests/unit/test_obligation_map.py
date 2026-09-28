# CMN-C1-226 — Unit Tests: ObligationMapNode

import json

from src.nodes.obligation_map_node import ObligationMapNode


def _state():
    return {
        "classification": json.dumps({"per_jurisdiction": [
            {"jurisdiction": "JP", "risk_class": "high-risk"},
            {"jurisdiction": "EU", "risk_class": "high-risk"},
        ]}),
        "retrieved_statutes": json.dumps([
            {"statute_id": "AI-Act", "article": "Art.6", "title": "Risk management system", "jurisdiction": "EU", "law_type": "obligation"},
            {"statute_id": "Japan-AI-Reg-2026", "article": "Art.5", "title": "Operator duties", "jurisdiction": "JP", "law_type": "obligation"},
            {"statute_id": "METI-Guide", "article": "§3", "title": "Best practice", "jurisdiction": "JP", "law_type": "recommendation"},
        ]),
    }


def test_self_skips_on_error():
    assert ObligationMapNode().execute({"error_code": "X"}) == {}


def test_maps_obligations_with_citation_and_law_type():
    out = ObligationMapNode().execute(_state())
    obs = json.loads(out["obligations"])
    assert len(obs) == 3
    eu = [o for o in obs if o["jurisdiction"] == "EU"][0]
    assert eu["basis_citation"] == "AI-Act Art.6"
    assert eu["law_type"] == "obligation"
    soft = [o for o in obs if o["law_type"] == "recommendation"]
    assert soft and soft[0]["basis_citation"] == "METI-Guide §3"


def test_filters_to_active_jurisdictions():
    s = _state()
    s["classification"] = json.dumps({"per_jurisdiction": [{"jurisdiction": "JP", "risk_class": "high-risk"}]})
    out = ObligationMapNode().execute(s)
    obs = json.loads(out["obligations"])
    assert all(o["jurisdiction"] in ("JP", "both") for o in obs)
