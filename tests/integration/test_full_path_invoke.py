# CMN-C1-226 — Integration: the production path, on the real SDK.
#
# Every test here builds the agent the way the Marketplace runner does —
# `Graph(config=<config/config.yaml dict>)`, no `llm_client` keyword — or adds one
# dependency at a time to prove a specific seam, then goes through the framework's
# own `invoke()` with a runner-shaped InvocationContext. Node-by-node tests cannot
# see what these pin: the `config["llm"]` seam, the kw > config precedence, and
# the named no-LLM degradation (no stub answer; deterministic artefacts kept).

import json
import pathlib

import pytest
import yaml

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services.service import (
    LLM_NOT_CONFIGURED,
    ConfigLLMAdapter,
    StubLLMClient,
    resolve_llm_client,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_QUESTION = "What obligations apply to high-risk ai systems?"


def _runner_config() -> dict:
    """The dict the runner passes: config/config.yaml as loaded, nothing added."""
    cfg = yaml.safe_load((_REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg, dict) and cfg, "config/config.yaml must load to a non-empty dict"
    return cfg


def _ctx() -> InvocationContext:
    return InvocationContext(caller_id="marketplace-user", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _invoke(agent, message: str = _QUESTION) -> dict:
    agent.compile()
    return agent.invoke(message, ctx=_ctx(), input_context={"conversation_history": []})


def _is_success(out: dict) -> bool:
    return str(out.get("status", "")).lower().endswith("success")


class _ScriptedLLM:
    """A config["llm"]-shaped client: `invoke(prompt) -> str`, no `generate`."""

    def __init__(
        self, reply: str = "SCRIPTED: high-risk systems need a conformity assessment [EU-AI-Act Art.6]."
    ) -> None:
        self.prompts: list[str] = []
        self.reply = reply

    def invoke(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class _RaisingLLM:
    def invoke(self, prompt: str) -> str:
        raise RuntimeError("upstream LLM failure (simulated)")


class TestBareRunnerConstruction:
    """`Graph(config=...)` alone — exactly what the Marketplace runner does."""

    def test_retrieved_statutes_without_llm_degrade_with_named_code(self):
        out = _invoke(Graph(config=_runner_config()))
        assert _is_success(out), out.get("status")
        assert out.get("retrieval_hit_count", 0) >= 1, "the seed KB did not retrieve"
        assert out.get("error_code") == LLM_NOT_CONFIGURED, out.get("error_code")
        assert not out.get("answer"), "an answer was produced with no LLM bound"
        text = str(out.get("output") or "")
        assert LLM_NOT_CONFIGURED in text and "statutes retrieved" in text
        assert "Per the cited statutes" not in text, "a stub answer leaked through"
        # The deterministic artefacts are still delivered, with provenance.
        cits = json.loads(out.get("citations") or "[]")
        assert cits and all(c.get("official_ref") for c in cits)
        assert out.get("disclaimer")
        assert out.get("audit_logged") is True

    def test_no_hits_and_no_llm_is_the_honest_out_of_scope_answer(self):
        out = _invoke(Graph(config=_runner_config()), message="今日の東京の天気は？")
        assert _is_success(out)
        assert out.get("error_code") is None, out.get("error_code")
        assert out.get("output")

    def test_bare_graph_binds_no_llm(self):
        assert Graph(config=_runner_config())._llm_client is None
        assert Graph()._llm_client is None


class TestConfigLlmSeam:
    """`config["llm"]` reaches the synthesis node; explicit kw wins over it."""

    def test_scripted_config_llm_writes_the_answer(self):
        llm = _ScriptedLLM()
        out = _invoke(Graph(config={**_runner_config(), "llm": llm}))
        assert _is_success(out) and out.get("error_code") is None, out
        assert llm.prompts, "the config['llm'] client was never called"
        synth_prompt = next(p for p in llm.prompts if "=== Statutes ===" in p)
        assert _QUESTION in synth_prompt
        assert "=== Classification ===" in synth_prompt
        assert llm.reply in out["output"], "the answer does not derive from the client's reply"
        assert out.get("citation_status") == "passed"

    def test_explicit_llm_client_wins_over_config_llm(self):
        config_llm = _ScriptedLLM(reply="FROM CONFIG [EU-AI-Act Art.6]")
        out = _invoke(
            Graph(
                config={**_runner_config(), "llm": config_llm},
                llm_client=StubLLMClient(canned="FROM EXPLICIT KW [EU-AI-Act Art.6]"),
            )
        )
        assert "FROM EXPLICIT KW" in out["output"]
        assert config_llm.prompts == [], "config['llm'] was called although an explicit client was given"

    def test_adapter_prefers_invoke_then_complete_and_coerces_message_content(self):
        class _Msg:
            content = "reply text"

        class _CompleteOnly:
            def complete(self, prompt, **kw):
                return _Msg()

        assert ConfigLLMAdapter(_ScriptedLLM(reply="x")).generate("p") == "x"
        assert ConfigLLMAdapter(_CompleteOnly()).generate("p") == "reply text"
        with pytest.raises(TypeError):
            ConfigLLMAdapter(object())
        assert resolve_llm_client(None, {"llm": None}) is None
        assert resolve_llm_client(None, None) is None
        stub = StubLLMClient()
        assert resolve_llm_client(None, {"llm": stub}) is stub  # already speaks generate()

    def test_a_raising_client_surfaces_as_a_named_error_not_a_guess(self):
        out = _invoke(Graph(config={**_runner_config(), "llm": _RaisingLLM()}))
        assert _is_success(out)  # degraded run: SUCCESS + error_code (post_process still audits)
        assert out.get("error_code") == "MAIN_PIPELINE_ERROR", out.get("error_code")
        assert out.get("output") and "MAIN_PIPELINE_ERROR" in out["output"]
        assert not out.get("answer"), "an answer was published although the LLM failed"


class TestNoLlmCitationGateFailsClosed:
    def test_uncited_answer_is_rejected_without_regeneration(self):
        from src.nodes.citation_validate_node import CitationValidateNode

        out = CitationValidateNode().execute(
            {"answer": "High-risk systems must register. No citation here.", "retrieval_hit_count": 2}
        )
        assert out["citation_status"] == "rejected"
        assert "must register" not in out["answer"]
