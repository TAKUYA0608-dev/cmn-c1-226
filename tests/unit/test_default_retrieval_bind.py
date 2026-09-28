# CMN-C1-226 — Regression: a bare Graph() (Marketplace runner / server.py
# construction form) must work with the illustrative seed KB instead of
# degrading every run to RETRIEVAL_BACKEND_MISSING.

from framework.schemas.invocation_context import InvocationContext, TrustLevel

from src.graph.graph import Graph
from src.nodes.regulation_retrieve_node import RegulationRetrieveNode
from src.services.service import InMemoryRegulationBackend, seed_regulation_kb


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, session_id="bare")


def _bare_agent():
    agent = Graph()  # no backend, no LLM — exactly what the Marketplace runner constructs
    agent.compile()
    return agent


def test_bare_graph_answers_from_seed_kb():
    out = _bare_agent().invoke("What obligations apply to high-risk ai systems?", ctx=_ctx())
    assert out["status"] == "success"
    assert out.get("error_code") != "RETRIEVAL_BACKEND_MISSING"
    assert out.get("retrieval_hit_count", 0) >= 1
    assert out.get("output"), "bare construction must still publish a non-empty output"


def test_bare_graph_survives_degenerate_input():
    out = _bare_agent().invoke(".", ctx=_ctx())
    assert out["status"] == "success"
    assert out.get("error_code") != "RETRIEVAL_BACKEND_MISSING"
    assert out.get("output"), "a degraded run must still publish a non-empty output"


def test_injected_backend_still_wins():
    """The DI path is unchanged: an injected backend is used, not the seed."""
    backend = InMemoryRegulationBackend()
    backend.add(
        [
            {
                "statute_id": "custom-1",
                "article": "Art.1",
                "title": "custom statute",
                "text": "customtoken only lives here",
                "jurisdiction": "JP",
                "effective_date": "2026-01-01",
                "kb_version": "t",
                "law_type": "obligation",
            }
        ]
    )
    node = RegulationRetrieveNode(retrieval_backend=backend)
    assert node._backend is backend
    hits = node._backend.search("customtoken")
    assert hits and hits[0]["statute_id"] == "custom-1"


_OFFICIAL_REF_PATTERN = (
    r"CELEX 3\d{4}[A-Z]\d+"  # EU: CELEX number (e.g. 32024R1689)
    r"|法律第\d+号"  # JP: statute number
    r"|e-Gov 法令ID \w+"  # JP: e-Gov law id
    r"|第\d+\.\d+版"  # JP soft law: versioned official document
)


class TestSeedProvenance:
    """The default seed of a compliance template must never present unsourced
    legal facts: every record carries a verifiable
    official identifier. This assertion keeps unsourced records from being
    reintroduced into the bare-Graph default.
    """

    def test_every_default_seed_record_has_a_verifiable_official_ref(self):
        import re as _re

        backend = seed_regulation_kb()
        # Read the seeded records directly off the backend store — search()
        # relevance filtering must not hide records from this audit.
        store = getattr(backend, "_records", None)
        assert store, "seed backend exposes no records to audit"
        assert len(store) >= 3, "default seed unexpectedly small"
        for rec in store:
            ref = rec.get("official_ref", "")
            assert ref and _re.search(_OFFICIAL_REF_PATTERN, ref), (
                f"seed record {rec.get('statute_id')}/{rec.get('article')} lacks a "
                f"verifiable official identifier: {ref!r}"
            )

    def test_eu_ai_act_application_dates_carry_their_legal_basis(self):
        """Regression guard: the EU AI Act staggers
        application — Annex III high-risk rules apply from 2027-12-02 per the
        current Commission timeline (Digital Omnibus amendment; supersedes the
        original Article 113(c) date), Article 50 transparency from the
        general application date 2026-08-02. Pinning both keeps a well-meaning
        edit from silently reintroducing an outdated application date as a
        legal fact.
        """
        backend = seed_regulation_kb()
        by_article = {(r["statute_id"], r["article"]): r for r in backend._records}
        art6 = by_article[("EU-AI-Act", "Art.6")]
        assert art6["effective_date"] == "2027-12-02"
        assert "Digital Omnibus" in art6["official_ref"]
        art50 = by_article[("EU-AI-Act", "Art.50")]
        assert art50["effective_date"] == "2026-08-02"

    def test_bare_graph_citations_carry_official_ref_end_to_end(self):
        """Provenance must reach the caller, not just the
        KB record — a bare Graph() run returns citations whose ``official_ref``
        lets the user verify the legal fact independently.
        """
        import json as _json

        from framework.schemas.invocation_context import InvocationContext, TrustLevel

        agent = Graph()
        agent.compile()
        ctx = InvocationContext(
            caller_id="e2e-provenance",
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        )
        out = agent.invoke("What obligations apply to high-risk ai systems?", ctx=ctx)
        assert str(out.get("status")).lower().endswith("success")
        # ``citations`` is a JSON-string envelope field (ADR-005 flat state);
        # provenance must survive all the way into it, whether or not the
        # citation gate withheld the prose answer.
        citations = _json.loads(out.get("citations") or "[]")
        assert citations, "bare-Graph run returned no citations"
        for c in citations:
            assert c.get("official_ref"), (
                f"citation {c.get('statute_id')}/{c.get('article')} reached the "
                f"caller without a verifiable official_ref"
            )
