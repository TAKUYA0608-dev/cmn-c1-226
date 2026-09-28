# CMN-C1-226 — Unit Tests: CitationValidateNode (S-3 citation gate)

import json

from src.nodes.citation_validate_node import CitationValidateNode, _uncited_claims
from src.services.service import StubLLMClient


def test_long_japanese_uncited_claim_is_flagged():
    # ADV-3: a long JP claim has ~1 whitespace token, so the char-length fallback
    # must catch it (otherwise it would slip past the word-count check).
    jp_uncited = "このAIシステムは高リスクに分類され多数の義務を直ちに履行する必要があります。"
    assert _uncited_claims(jp_uncited) == [jp_uncited]
    # the same claim WITH a statute citation must NOT be flagged
    jp_cited = "このAIシステムは高リスクに分類されます [Japan-AI-Reg-2026 Art.5]。"
    assert _uncited_claims(jp_cited) == []


def _state(answer, hits=2):
    return {"answer": answer, "retrieval_hit_count": hits}


def test_all_cited_passes():
    out = CitationValidateNode().execute(_state(
        "Under EU the system is high-risk [AI-Act Art.6]. JP operator duties apply [Japan-AI-Reg-2026 Art.5]."))
    assert out["citation_status"] == "passed"


def test_japanese_citation_form_accepted():
    out = CitationValidateNode().execute(_state(
        "個人データの取扱いには通知義務がある [APPI-2026 第20条]。"))
    assert out["citation_status"] == "passed"


def test_zero_hit_refusal_skipped():
    # a 0-hit out-of-scope refusal is a meta-statement, not a grounded claim
    out = CitationValidateNode().execute(_state("This is outside the knowledge base and asserts nothing.", hits=0))
    assert out["citation_status"] == "passed"


def test_self_skips_on_error():
    out = CitationValidateNode().execute({"answer": "x", "error_code": "E", "retrieval_hit_count": 2})
    assert out["citation_status"] == "passed"


def test_uncited_regenerated():
    # first answer uncited; regeneration adds a citation → regenerated
    fixed = StubLLMClient(canned="The system is high-risk and carries documentation duties [AI-Act Art.6].")
    out = CitationValidateNode(llm_client=fixed).execute(_state(
        "The system is definitely high risk and you must comply with many obligations immediately."))
    assert out["citation_status"] == "regenerated"
    assert "[AI-Act Art.6]" in out["answer"]


def test_uncited_rejected_when_regeneration_still_uncited():
    bad = StubLLMClient(canned="Still an entirely uncited compliance assertion with no statute reference whatsoever.")
    out = CitationValidateNode(llm_client=bad).execute(_state(
        "This system is definitely high risk and you must do many things without any citation at all."))
    assert out["citation_status"] == "rejected"
    assert "withholding" in out["answer"].lower()
