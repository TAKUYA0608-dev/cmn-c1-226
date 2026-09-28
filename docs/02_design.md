# Template Design Specification — CMN-C1-226

**Enterprise Japan AI Regulation 2026 Compliance Q&A Agent** (`flagship`)

> Source proposal: `docs/01_proposal.md` (SoT: note #942508 JP / #942518 EN).
> Pre-design scope (legal/architect inputs) recorded in `Idea/.../pre_design_review_request.md`.
> The regulatory-scope assumptions adopted in lieu of a dedicated compliance-officer review are
> listed in §1.2 — they are conservative defaults, revisable when domain sign-off arrives.

## Position in AgentCore Architecture

- **Agent Class**: `JapanAIRegulationComplianceQAAgent`
- **L1 Base**: `AgentBaseGraph` (L1 direct, per the 2026-05-18 platform decision; reference RAG pattern = a sibling template)
- **Category**: Cat 1 — single capability: *regulatory-compliance Q&A with classification + obligation mapping*
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state, config=None) -> dict` override only)
  - Graph: composition (`register_nodes()` slot substitution; framework owns edges + routing)

## 1. Capability & Scope

### 1.1 What it does

NL Q&A over a curated regulatory KB (Japan AI Regulation 2026 + EU AI Act + APPI 2026 + METI
Guidelines v1.2 + Japan SIer interpretations). Given a question + an AI-system description +
a jurisdiction, it:

1. classifies the AI system against the regulatory risk taxonomy (per jurisdiction, with confidence),
2. retrieves the governing statutes/articles (jurisdiction-aware RAG),
3. maps the classification to its obligation set,
4. analyses the AI system against the obligations → a gap list,
5. produces a compliance gap checklist + a regulation-aligned deadline calendar, and
6. returns a citation-backed answer where **every regulatory claim cites a statute/article**, with a
   mandatory "not legal advice" disclaimer and a human-review flag for low-confidence / high-risk cases.

### 1.2 Adopted scope assumptions (conservative defaults — §0 of pre-design package)

| Ref | Decision | Rationale |
|-----|----------|-----------|
| A-2 | **Jurisdictions = JP / EU only.** US state law, sector regimes (PMDA/FSA), case law, tax, individual legal opinions → **out of scope, refused** | bound liability; keep the capability generic |
| A-1 | **No statute article numbers / effective dates are hard-coded.** They live in the KB as data (`effective_date` / `kb_version` per record); the agent cites whatever the KB holds | KB-driven design ⇒ structurally avoids stale/incorrect legal facts; the customer curates the corpus |
| A-4 | Output **labels each basis as `obligation` / `recommendation` / `interpretation`** (hard law vs METI soft law vs SIer interpretation) | never let soft law read as a binding duty |
| B-2 | On JP↔EU conflict the agent **presents both jurisdictions and recommends expert consultation** — it never instructs "take the stricter side" | template must not give definitive legal direction |
| C-1 | `confidence < τ` (τ = **0.75**) **OR** `risk_class ∈ {prohibited, high-risk}` ⇒ `requires_human_review = true` | flagship-conservative; high-stakes calls always seen by a human |
| C-1 | Escalation is a **structured `requires_human_review` flag + degraded response**, NOT langgraph `interrupt()` HITL | Cat 1 component reusability — the parent orchestrator decides; `hitl.enabled` stays absent |
| C-2 | **S-3 citation gate**: every regulatory claim sentence **and** every `risk_class` verdict must carry a `[<statute> <article>]` citation | hallucination = top risk (#1) |
| D | A **`disclaimer` field** ("this is not legal advice; verify latest amendments; do not use as sole basis") is attached to every output | regulatory-advice-adjacent output |

### 1.3 Out of scope (refused, not guessed)

Non-JP/EU jurisdictions; sector-specific regulators; predictions of litigation/administrative outcomes;
tax/accounting/labor treatment; contract drafting / company-specific legal opinions. A query landing
here returns a structured out-of-scope response (no obligations asserted).

## 2. Architecture Overview

### 2.1 Node Configuration — 7 steps → 3 writable slots

The framework exposes 5 fixed slots (`initialize / pre_process / main / post_process / finalize`) and
owns `initialize`, `finalize`, edge wiring, and routing. The 7 proposal steps compose into the 3
writable slots:

| Slot | Node (class) | Composes | Responsibility |
|------|--------------|----------|----------------|
| initialize | `InitializeNode` (framework default) | — | framework bootstrap |
| **pre_process** | `QueryNormalizeNode` | step 1 | S-1 input boundary: validate question + `ai_system_description` + jurisdiction; NFKC normalize; **reject prompt-injection**; redact system identity to a shape |
| **main** | `ComplianceMainNode` (composite) | steps 2–6 | `AISystemClassify → RegulationRetrieve → ObligationMap → GapAnalyze → ComplianceOutput` |
| **post_process** | `CompliancePostNode` (composite) | step 7 | `CitationValidate` (S-3 citation gate) `→ AuditLog` (S-4) |
| finalize | `FinalizeNode` (framework default) | — | framework teardown |

Composite nodes thread a working state through their sub-nodes and return **only the accumulated
deltas** (node contract: "return changed fields only"). Each sub-node **self-skips when `error_code`
is set**, so the first fatal error short-circuits the rest of the pipeline while `AuditLog` still fires.

### 2.2 main-slot sub-nodes (steps 2–6)

| # | Sub-node | Input | Output (delta) | Notes |
|---|----------|-------|----------------|-------|
| 2 | `AISystemClassifyNode` | `validated_question`, `ai_system_shape`, `jurisdiction` | `classification` (per-jurisdiction `risk_class` + `confidence` + `basis_citations`), `requires_human_review` (provisional) | LLM-essential; confidence drives C-1 |
| 3 | `RegulationRetrieveNode` | `validated_question`, `jurisdiction`, `classification` | `retrieved_statutes`, `retrieval_hit_count` | jurisdiction-aware RAG (DI backend); PB-3 external boundary; S-4 event |
| 4 | `ObligationMapNode` | `classification`, `retrieved_statutes` | `obligations` (per-jurisdiction, each `{text, basis_citation, law_type}`) | LLM-essential; `law_type ∈ {obligation, recommendation, interpretation}` (A-4) |
| 5 | `GapAnalyzeNode` | `ai_system_shape`, `obligations` | `gaps` (unmet obligations + severity) | LLM-essential |
| 6 | `ComplianceOutputNode` | all above | `answer`, `citations`, `gap_checklist`, `deadline_calendar`, `conflicts`, `disclaimer`, final `requires_human_review` | assembles the cited answer + checklist + calendar; surfaces JP↔EU conflicts (B-2); attaches disclaimer (D); 0-hit ⇒ out-of-scope |

### 2.3 Data Flow

```
START → initialize → pre_process(QueryNormalize, S-1)
      → main(AISystemClassify → RegulationRetrieve → ObligationMap → GapAnalyze → ComplianceOutput)
      → post_process(CitationValidate [S-3] → AuditLog [S-4])
      → finalize → END
                                            (any node sets error_code ⇒ downstream self-skip; AuditLog still fires)
```

## 3. State Schema (`src/schemas/state.py`)

`ComplianceQAState(AgentState)` — flat TypedDict; all fields Optional primitives or JSON-serialized
strings (msgpack-safe). **No JWT / API keys / Pydantic / InvocationContext in state.** The AI-system
description is **never stored raw** — only `ai_system_shape` (presence flags, no identity/PII).

| Field | Type | Written by |
|-------|------|-----------|
| `question` | str | caller |
| `ai_system_description` | str (JSON) | caller (read-only; redacted at pre_process) |
| `ai_system_shape` | str (JSON; flags only) | QueryNormalize |
| `jurisdiction` | str (`JP`/`EU`/`both`) | caller / QueryNormalize |
| `validated_question` | str | QueryNormalize |
| `classification` | str (JSON; per-jurisdiction risk_class+confidence+basis) | AISystemClassify |
| `requires_human_review` | bool | AISystemClassify (provisional) → ComplianceOutput (final) |
| `retrieved_statutes` | str (JSON) | RegulationRetrieve |
| `retrieval_hit_count` | int | RegulationRetrieve |
| `obligations` | str (JSON; each {text, basis_citation, law_type}) | ObligationMap |
| `gaps` | str (JSON) | GapAnalyze |
| `answer` | str (inline `[<statute> <article>]` citations) | ComplianceOutput |
| `citations` | str (JSON) | ComplianceOutput |
| `gap_checklist` | str (JSON) | ComplianceOutput |
| `deadline_calendar` | str (JSON) | ComplianceOutput |
| `conflicts` | str (JSON) | ComplianceOutput |
| `disclaimer` | str | ComplianceOutput |
| `citation_status` | str (`passed`/`regenerated`/`rejected`) | CitationValidate |
| `uncited_claims` | str (JSON) | CitationValidate |
| `audit_logged` | bool | AuditLog |
| `error_code` / `error_message` | str | any node |

## 4. Security (5-layer, the platform security-layer contract)

- **S-1** `QueryNormalizeNode`: empty / length / **prompt-injection reject**; `required_trust_level = VERIFIED_EXTERNAL`.
- **S-2** framework `@final` PII input scan (FunctionNode).
- **S-3** two layers: framework `@final` credential scan **+** the domain **citation gate**
  (`CitationValidateNode`): every regulatory claim sentence and every `risk_class` verdict must carry a
  `[<statute> <article>]` citation → else regenerate once → else **reject** (replace with a safe response).
- **S-4** `AuditLogNode` emits one redacted event: classification shape (risk_class, jurisdiction,
  confidence band, citation count, gap count, `requires_human_review`) — **never** the AI-system identity
  or PII. Defensive import (`shared.utils.audit_logger` + stderr fallback).
- **S-5** no credentials in state/code; secrets via `requires.secrets` (`LLM_API_KEY`, `VECTOR_STORE_DSN`).

**Execution order** per node (framework `__call__`): S-1 trust gate → S-4 node_start → S-2 input gate →
`execute()` → S-3 output gate → S-4 node_complete. Templates emit only domain S-4 events inside `execute()`.

## 5. Composition consistency (criterion #9)

`ComplianceMainNode` / `CompliancePostNode` are `FunctionNode` composites (not `GraphNode`), so no
sub-graph trust delegation is involved. **Error-propagation strategy**: each sub-node returns `{}` (self-skip)
when `error_code` is set; the composite carries the first error forward and always returns
`status = success` so routing reaches `post_process`, where `AuditLog` records the outcome (incl. the
error). `ComplianceOutputNode` emits a structured out-of-scope / human-review response rather than raising.

## 6. Dependency injection (offline-testability)

Retrieval + LLM are injected (`RegulationRetrievalBackend` / `LLMClient` Protocols). Production binds the
real vector store (`VECTOR_STORE_DSN`) + platform LLM client (`LLM_API_KEY` via `ctx.secrets`); tests bind
`InMemoryRegulationBackend` + `StubLLMClient`. This realises PB-3 (external-service boundary) deterministically.

## 7. Confidence & human-review policy (C-1)

`requires_human_review = (any per-jurisdiction confidence < 0.75) OR (any risk_class ∈ {prohibited, high-risk})`.
When true, `ComplianceOutputNode` withholds a *final* obligation determination and returns a "human review
required" answer carrying the provisional classification + the disclaimer. The flag is structured output —
a parent Cat 2 orchestrator decides whether to route to a human; this template does not block on `interrupt()`,
so `hitl.enabled` stays absent (criterion #12 skipped).

## 8. Out-of-scope / 0-hit handling

`jurisdiction ∉ {JP, EU, both}` or a query about an out-of-scope regime → QueryNormalize/ComplianceOutput
return a structured out-of-scope response (no obligations). `retrieval_hit_count == 0` → "no basis in the
current KB"; the citation gate skips (a refusal is not a grounded claim, like sibling templates).

## 9. Error handling & degradation

Backend/LLM failure → `error_code` (`RETRIEVAL_BACKEND_MISSING` / `RETRIEVAL_FAILED` / …), downstream
self-skip, `AuditLog` still fires. No exception escapes `invoke()` for an in-flow domain error.

LLM binding (ADR-6): explicit `llm_client` > `config["llm"]` (adapted by
`service.resolve_llm_client`) > none. No stub LLM is ever bound by default (the stub is
test-only, explicit injection).

| code | Set by | Meaning | Output text |
|------|--------|---------|-------------|
| `LLM_NOT_CONFIGURED` (`error_code`) | ComplianceOutput | statutes retrieved but no LLM bound (`llm_client` / `config["llm"]`); the deterministic `citations` / `gap_checklist` / `deadline_calendar` / `conflicts` / `disclaimer` are still written, the prose answer is not; CitationValidate passes through, AuditLog fires | notice naming the code + statute count + which envelope fields are populated; **never a stub answer** |
| (no LLM, uncited answer) | CitationValidate | cannot regenerate → rejects outright (`citation_status=rejected`) | the standard rejection text |
| `MAIN_PIPELINE_ERROR` (`error_code`) | ComplianceMain | a sub-node raised (incl. **a bound LLM that raised** — the adapter never swallows it) | generic notice naming the code; no answer |

## Design Decision Record

| Decision | Chosen | Rationale |
|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | knowledge-QA capability; not autonomous (fixed pipeline) |
| Composition pattern | `FunctionNode` composites in main/post slots | 7 steps → 3 writable slots; no sub-graph needed |
| Escalation mechanism | structured `requires_human_review` flag (NOT `interrupt()` HITL) | Cat 1 reusability; parent orchestrator owns the human loop |
| Legal facts | KB data (`effective_date`/`kb_version`), never hard-coded | structurally avoids stale/incorrect statute facts |
| Conflict policy | both-sides + expert consult (never "take stricter") | template must not give definitive legal direction |
| ADR-6 LLM binding (2026-09-03): explicit kw > `config["llm"]` > none, named `LLM_NOT_CONFIGURED` (was: silent `StubLLMClient` default) | Option B | the Marketplace runner constructs `Graph(config=config.yaml)` and the fleet entry point can only place the Azure client under `config["llm"]`; a silent stub ran on the Pod even with keys registered (its output was withheld by the citation gate, so every run looked like a rejection). The prose answer is LLM-written, so with no LLM the gap is named (SUCCESS + error_code + notice) and only the deterministic artefacts are delivered |

## Import Isolation Confirmation
- [x] Template does not import `agenticstar` (Level 0)
- [x] Import targets: `framework/` and `shared/` only
