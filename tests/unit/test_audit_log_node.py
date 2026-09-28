# CMN-C1-226 — Unit Tests: AuditLogNode (S-4, impl)
#
# AuditLog calls emit_trace_event(), which routes to the platform AuditSink when
# shared.utils.audit_logger is present (CI / published SDK) or to a stderr sink
# under a local SDK stub. To stay environment-independent, spy on the node's
# emit_trace_event rather than asserting on a specific sink (stderr).

import json

import src.nodes.audit_log_node as audit_mod
from src.nodes.audit_log_node import AuditLogNode


def _capture(monkeypatch):
    events = []
    monkeypatch.setattr(audit_mod, "emit_trace_event",
                        lambda et, payload=None, state=None: events.append((et, payload, state)))
    return events


def test_emits_redacted_audit_event_no_pii(monkeypatch):
    events = _capture(monkeypatch)
    state = {
        "validated_question": "is our biometric recruitment system high risk",
        "answer": "Under EU it is high-risk [AI-Act Art.6].",
        "jurisdiction": "both",
        "citations": json.dumps([{"marker": "AI-Act Art.6"}]),
        "gap_checklist": json.dumps([{"item": "risk mgmt"}]),
        "conflicts": json.dumps([]),
        "retrieval_hit_count": 2,
        "citation_status": "passed",
        "requires_human_review": True,
        "classification": json.dumps({"per_jurisdiction": [
            {"jurisdiction": "JP", "risk_class": "high-risk", "confidence": 0.85},
            {"jurisdiction": "EU", "risk_class": "high-risk", "confidence": 0.85},
        ]}),
        "session_id": "S-1",
    }
    out = AuditLogNode().execute(state)
    assert out["audit_logged"] is True

    assert len(events) == 1
    event_type, payload, _state = events[0]
    assert event_type == "agent_invoke_complete"
    assert payload["citation_count"] == 1
    assert payload["gap_count"] == 1
    assert payload["requires_human_review"] is True
    assert payload["classification_shape"]["risk_classes"] == ["high-risk", "high-risk"]
    # no raw question / answer text leaks — only lengths + shape
    blob = json.dumps(payload)
    assert "biometric recruitment system" not in blob
    assert "high-risk [AI-Act" not in blob


def test_emits_even_on_error_path(monkeypatch):
    events = _capture(monkeypatch)
    out = AuditLogNode().execute({"error_code": "INPUT_EMPTY"})
    assert out["audit_logged"] is True
    assert len(events) == 1
    _et, payload, _state = events[0]
    assert payload["error_code"] == "INPUT_EMPTY"
