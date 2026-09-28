# Test Specification — CMN-C1-226 JapanAIRegulationComplianceQAAgent

## Test Strategy

- **Test types**: per-node unit + agent-level e2e (`tests/unit/`) + Proof-of-Boundary (`tests/proof_of_boundary/`).
- **Total**: 92 tests passing (offline — retrieval + LLM are dependency-injected stubs).
- **Determinism**: `InMemoryRegulationBackend` + `StubLLMClient(canned=...)` make every path reproducible.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Where |
|-------|------|----------------|-------|
| TC-01 | State contract: flat TypedDict, no Pydantic/dataclass | PASS (AST scan) | `proof_of_boundary/test_state_safety.py` |
| TC-02 | Input rejected on invalid input (empty/too-long/injection) | `error_code` set, downstream self-skip | `test_query_normalize.py`, `test_agent.py::test_injection_rejected_end_to_end` |
| TC-03 | No JWT/credential in State | no credential-like fields (AST scan) | `proof_of_boundary/test_state_safety.py` |
| TC-04 | InvocationContext via configurable only (not in State) | not stored in State | by construction (state schema has no ctx field) |
| TC-05 | Domain `emit_trace_event()` called for side effects | event emitted (classify/retrieve/obligation/gap/output/audit) | `test_audit_log_node.py` (spy), node unit tests |
| TC-06 | `_security_gate_input()` non-bypassable (`@final`) | framework-enforced (FunctionNode) | framework guarantee |
| TC-07 | `_security_gate_output()` non-bypassable (`@final`) | framework-enforced (FunctionNode) | framework guarantee |
| TC-08 | `required_trust_level` declared + enforced | `VERIFIED_EXTERNAL` (agent.yaml + S-1 gate) | `config/agent.yaml`, framework S-1 |

## Domain Test Coverage (by node)

| Node | Key cases |
|------|-----------|
| QueryNormalize | empty / too-long / injection (question + description) reject; jurisdiction normalize (JP/EU/both/unsupported); **description redacted to shape (no raw values)**; NFKC + whitespace |
| AISystemClassify | prohibited/high-risk/limited/minimal signal → risk_class + confidence; **human-review when high-stakes OR confidence<0.75**; per-jurisdiction expansion; unsupported jurisdiction → empty |
| RegulationRetrieve | missing backend error; jurisdiction filter; unsupported→0 hits; backend exception degrades; self-skip on error |
| ObligationMap | basis_citation + law_type (obligation/recommendation/interpretation); filter to active jurisdictions |
| GapAnalyze | unmet→gap with severity by law_type; evidenced→not a gap |
| ComplianceOutput | 0-hit out-of-scope; cited answer + citations + gap_checklist + deadline_calendar; **disclaimer always present + human-review note**; **JP↔EU conflict surfaced, never collapsed**; minimal/clear → no human review |
| CitationValidate (S-3) | all-cited pass; Japanese `第N条` form; 0-hit refusal skipped; regenerate-once; **reject when still uncited** |
| AuditLog (S-4) | redacted payload (counts + classification shape, **no raw question/answer/PII**); fires on error path |
| ComplianceMainNode | runs all 5 steps; self-skip on upstream error; returns deltas only; node contract (`execute(self, state)`) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Result |
|-------|----------|------|--------|
| PB-2 | State serialization (msgpack-safe) | AST scan: no Pydantic/dataclass/credential | `test_state_safety.py` PASS |
| PB-3 | L1 → external service (statute KB) | retrieved record surfaces to output; missing backend degrades (not silent) | `test_external_service.py` PASS |
| PB-4 | Import isolation (no Level 0) | AST scan: 0 violations | `test_import_isolation.py` PASS |
| PB-6 | Invoke execution order | `Initialize→QueryNormalize→ComplianceMain→CompliancePost→Finalize`; post after main, before finalize | `test_invoke_order.py` PASS |
| PB (domain) | S-3 citation gate end-to-end | cited→pass; persistently uncited→rejected + audited | `test_citation_gate.py` PASS |

## Out of scope for automated tests

- PB-7 (HITL interrupt): N/A — this template uses a structured `requires_human_review` flag, not `interrupt()`; `hitl.enabled` is absent (criterion #12 skipped).
- Real vector-store / real LLM integration: validated at STG (deploy) stage, not in unit CI.
