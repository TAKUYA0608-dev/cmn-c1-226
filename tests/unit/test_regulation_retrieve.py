# CMN-C1-226 — Unit Tests: RegulationRetrieveNode (jurisdiction-aware RAG, DI)

import json

from src.nodes.regulation_retrieve_node import RegulationRetrieveNode
from src.services.service import InMemoryRegulationBackend


def _backend():
    b = InMemoryRegulationBackend()
    b.add([
        {"statute_id": "AI-Act", "article": "Art.6", "title": "high risk", "text": "biometric recruitment high risk",
         "jurisdiction": "EU", "effective_date": "2026-08-02", "kb_version": "2024/1689", "law_type": "obligation"},
        {"statute_id": "Japan-AI-Reg-2026", "article": "Art.5", "title": "operator duties", "text": "biometric operator duties",
         "jurisdiction": "JP", "effective_date": "2026-06-01", "kb_version": "v1", "law_type": "obligation"},
    ])
    return b


def test_missing_backend_binds_seed_kb():
    # Contract change (default retrieval bind): no injected backend now means the
    # illustrative seed KB, not RETRIEVAL_BACKEND_MISSING — a bare Graph() (the
    # Marketplace runner / server.py construction form) must be a working form.
    out = RegulationRetrieveNode(retrieval_backend=None).execute(
        {"validated_question": "high-risk ai systems obligations", "jurisdiction": "both"})
    assert "error_code" not in out
    assert out["retrieval_hit_count"] >= 1


def test_self_skips_on_error():
    assert RegulationRetrieveNode(retrieval_backend=_backend()).execute({"error_code": "X"}) == {}


def test_retrieves_both_jurisdictions():
    out = RegulationRetrieveNode(retrieval_backend=_backend()).execute(
        {"validated_question": "biometric recruitment", "jurisdiction": "both"})
    assert out["retrieval_hit_count"] == 2
    recs = json.loads(out["retrieved_statutes"])
    assert {r["jurisdiction"] for r in recs} == {"EU", "JP"}


def test_jurisdiction_filter():
    out = RegulationRetrieveNode(retrieval_backend=_backend()).execute(
        {"validated_question": "biometric", "jurisdiction": "JP"})
    recs = json.loads(out["retrieved_statutes"])
    assert all(r["jurisdiction"] == "JP" for r in recs)


def test_unsupported_jurisdiction_zero_hits():
    out = RegulationRetrieveNode(retrieval_backend=_backend()).execute(
        {"validated_question": "biometric", "jurisdiction": "US"})
    assert out["retrieval_hit_count"] == 0


def test_backend_exception_degrades():
    class Boom:
        def search(self, *a, **k):
            raise RuntimeError("down")
    out = RegulationRetrieveNode(retrieval_backend=Boom()).execute({"validated_question": "q", "jurisdiction": "both"})
    assert out["error_code"] == "RETRIEVAL_FAILED"
