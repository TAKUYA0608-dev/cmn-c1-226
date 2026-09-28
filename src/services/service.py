"""CMN-C1-226 — service layer: regulatory KB retrieval + LLM boundaries (DI).

The two external services (the statute KB retriever, and the LLM) are injected so
the template is offline-testable and deployment-agnostic. Production binds the
real vector store + platform LLM client; tests bind the in-memory / stub
implementations defined here.

Legal facts (article numbers, effective dates) are KB *data*, never hard-coded
here — see docs/02_design.md §1.2 (A-1). No `agenticstar` (Level 0) imports; no
`framework.*` dependency — pure domain logic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# Supported jurisdictions (docs/02_design.md §1.2 A-2). Anything else is refused.
JURISDICTIONS: tuple[str, ...] = ("JP", "EU", "both")

# Regulatory risk taxonomy (EU AI Act tiers; JP cross-walk lives in the KB).
RISK_CLASSES: tuple[str, ...] = ("prohibited", "high-risk", "limited", "minimal")

# Classes that always force human review regardless of confidence (C-1).
HIGH_STAKES_CLASSES: frozenset[str] = frozenset({"prohibited", "high-risk"})

# Confidence threshold τ below which human review is required (C-1).
CONFIDENCE_THRESHOLD: float = 0.75

# Legal-force labels for an obligation's basis (A-4): hard law vs soft law vs interpretation.
LAW_TYPES: tuple[str, ...] = ("obligation", "recommendation", "interpretation")


@runtime_checkable
class RegulationRetrievalBackend(Protocol):
    """Statute KB retriever boundary.

    `search` returns a ranked list of statute record dicts, each shaped:
        {"statute_id": str, "article": str, "title": str, "text": str,
         "score": float, "jurisdiction": str, "effective_date": str,
         "kb_version": str, "law_type": str}
    `jurisdiction`, when given, filters / biases to that jurisdiction ("both" = no filter).
    """

    def search(self, query: str, top_k: int = 8, jurisdiction: str | None = None) -> list[dict[str, Any]]: ...


@runtime_checkable
class LLMClient(Protocol):
    """LLM boundary — `generate(prompt) -> str`."""

    def generate(self, prompt: str) -> str: ...


class InMemoryRegulationBackend:
    """In-memory RegulationRetrievalBackend for tests / local runs.

    Seed with `add([record, ...])`; `search` ranks by naive lexical overlap and,
    when a `jurisdiction` is given (and not "both"), filters to that jurisdiction.
    """

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    def add(self, records: list[dict[str, Any]]) -> None:
        self._records.extend(records)

    def search(self, query: str, top_k: int = 8, jurisdiction: str | None = None) -> list[dict[str, Any]]:
        terms = {t for t in query.lower().split() if t}
        pool = self._records
        if jurisdiction and jurisdiction != "both":
            pool = [r for r in pool if r.get("jurisdiction") == jurisdiction]

        def score(r: dict[str, Any]) -> float:
            words = set(str(r.get("text", "")).lower().split()) | set(str(r.get("title", "")).lower().split())
            return round((len(terms & words) / len(terms)) if terms and words else 0.0, 4)

        ranked = sorted(
            ({**r, "score": score(r)} for r in pool),
            key=lambda r: r["score"],
            reverse=True,
        )
        return [r for r in ranked if r["score"] > 0.0][:top_k]


class StubLLMClient:
    """Deterministic stub LLM for tests / local runs — templated, citation-bearing.

    Never bound by default: with no LLM resolved (see `resolve_llm_client`) the
    synthesis step degrades with `LLM_NOT_CONFIGURED` instead of running on this
    stub. Tests inject it explicitly.
    """

    def __init__(self, canned: str | None = None) -> None:
        self._canned = canned

    def generate(self, prompt: str) -> str:
        if self._canned is not None:
            return self._canned
        tail = prompt.strip().splitlines()[-1][:200] if prompt.strip() else ""
        # The cited statute is deliberately fictitious and never present in any
        # retrieval result: the citation gate therefore always withholds the
        # stub's free-running output. Do NOT change this to a real statute id —
        # a resolvable citation would let unverified stub prose through the
        # gate as if it were a grounded answer.
        return f"Per the cited statutes: {tail} [Stub-Unverified Art.0]"


# ── LLM seam: config["llm"] → LLMClient ───────────────────────────────────────
# error_code set by ComplianceOutput when statutes were retrieved but no LLM is bound.
LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"


def _as_text(result: Any) -> str:
    """Coerce a client reply to text: str as-is, message-like objects via `.content`."""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return "" if result is None else str(result)


class ConfigLLMAdapter:
    """Adapt a `config["llm"]` object to this template's `LLMClient` Protocol.

    The fleet entry point places a lazily-resolved chat client under `config["llm"]`
    that answers `invoke(prompt) -> str` (and `complete(prompt, **kw) -> str`); this
    template's nodes speak `generate(prompt) -> str`. The adapter forwards to `invoke`
    first, then `complete`, and never swallows the client's exceptions — a configured
    but failing LLM must surface, not silently degrade.
    """

    def __init__(self, client: Any) -> None:
        call = getattr(client, "invoke", None)
        if not callable(call):
            call = getattr(client, "complete", None)
        if not callable(call):
            raise TypeError(
                "config['llm'] must expose generate(prompt), invoke(prompt) or "
                f"complete(prompt); got {type(client).__name__}"
            )
        self._client = client
        self._call = call

    def generate(self, prompt: str) -> str:
        return _as_text(self._call(prompt))


def resolve_llm_client(explicit: LLMClient | None, config: Any) -> LLMClient | None:
    """Precedence: explicit `llm_client` kw > `config["llm"]` > None.

    None means "no LLM bound": ComplianceOutput degrades with `LLM_NOT_CONFIGURED`
    (SUCCESS + error_code + non-empty notice; the deterministic citations / gap
    checklist / deadline calendar / conflicts / disclaimer are still produced, the
    prose answer is not) and CitationValidate rejects an uncited answer outright
    instead of regenerating. An object under `config["llm"]` that answers none of
    generate/invoke/complete raises at construction (misconfiguration is not a
    reason to degrade quietly).
    """
    if explicit is not None:
        return explicit
    candidate = config.get("llm") if isinstance(config, dict) else None
    if candidate is None:
        return None
    if isinstance(candidate, LLMClient):
        return candidate
    return ConfigLLMAdapter(candidate)


def seed_regulation_kb() -> InMemoryRegulationBackend:
    """Return an InMemoryRegulationBackend seeded with representative statute records.

    A compact, illustrative slice (EN + JP keyword tags) — enough for a bare
    ``Graph()`` (the form both server.py and the Marketplace runner construct) to
    answer instead of degrading every run to RETRIEVAL_BACKEND_MISSING. Statute
    facts remain KB *data* (docs/02_design.md §1.2 A-1): production replaces this
    with the full curated statute KB; the record shape is identical.
    """
    # Every default-seed record MUST carry a verifiable official identifier in
    # ``official_ref`` (enforced by tests/unit/test_default_retrieval_bind.py).
    # A compliance template must never present unsourced legal facts as its
    # bare-Graph default — records without an official, checkable identifier
    # do not belong here.
    backend = InMemoryRegulationBackend()
    backend.add(
        [
            {
                "statute_id": "EU-AI-Act",
                "article": "Art.6",
                "title": "High-risk AI system classification",
                "text": "high-risk ai systems classification obligations annex iii biometric employment recruitment "
                "credit scoring 高リスク AI システム 分類 義務",
                "jurisdiction": "EU",
                # Application date for Annex III high-risk rules per the current
                # Commission timeline reflecting the Digital Omnibus amendment
                # to Regulation (EU) 2024/1689: 2 December 2027. (The original
                # Article 113(c) date, 2 August 2027, was superseded by the
                # Digital Omnibus; the Regulation's general application date,
                # 2 August 2026, governs Article 50 below.)
                "effective_date": "2027-12-02",
                "kb_version": "2024/1689-seed",
                "law_type": "obligation",
                "official_ref": "Regulation (EU) 2024/1689 (CELEX 32024R1689), Article 6; "
                "Annex III high-risk application from 2027-12-02 per the Digital "
                "Omnibus amendment (Article 113 as amended)",
            },
            {
                "statute_id": "EU-AI-Act",
                "article": "Art.50",
                "title": "Transparency obligations",
                "text": "transparency obligations disclosure ai-generated content chatbot user notification "
                "透明性 義務 開示 通知",
                "jurisdiction": "EU",
                "effective_date": "2026-08-02",
                "kb_version": "2024/1689-seed",
                "law_type": "obligation",
                "official_ref": "Regulation (EU) 2024/1689 (CELEX 32024R1689), Article 50",
            },
            {
                "statute_id": "JP-APPI",
                "article": "Art.27",
                "title": "Third-party provision of personal data",
                "text": "personal data third-party provision consent cross-border transfer appi "
                "個人データ 第三者提供 同意 越境移転",
                "jurisdiction": "JP",
                "effective_date": "2022-04-01",
                "kb_version": "v1-seed",
                "law_type": "obligation",
                "official_ref": "個人情報の保護に関する法律（平成15年法律第57号）第27条 (e-Gov 法令ID 415AC0000000057)",
            },
            {
                "statute_id": "JP-AI-Guidelines",
                "article": "Part 2",
                "title": "AI business operator guidelines (soft law)",
                "text": "ai business operator guidelines governance accountability soft law meti "
                "AI 事業者 ガイドライン ガバナンス 説明責任",
                "jurisdiction": "JP",
                "effective_date": "2024-04-19",
                "kb_version": "v1.0-seed",
                "law_type": "recommendation",
                "official_ref": "AI事業者ガイドライン（第1.0版）経済産業省・総務省 2024-04-19",
            },
        ]
    )
    return backend
