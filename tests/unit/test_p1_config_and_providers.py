"""Unit tests for Phase P1: L0 configuration, error hierarchy, and L1 provider layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx

from orchestra.config import constants as c
from orchestra.config.models_registry import ModelMetadata, ModelsRegistry
from orchestra.config.settings import load_settings
from orchestra.errors import (
    AuthFailed,
    BadRequest,
    ConfigurationError,
    ContentFiltered,
    ContextOverflow,
    ModelNotFoundError,
    ProviderUnavailable,
    RateLimited,
    StreamInterrupted,
    TruncatedOutput,
)
from orchestra.providers.base import (
    ChatMessage,
    ChatRequest,
    ContentDelta,
    FinishDelta,
    LLMProvider,
    ReasoningDelta,
    ToolCallDelta,
    ToolDefinition,
    UsageDelta,
    compute_key_fingerprint,
)
from orchestra.providers.capabilities import CapabilityProber
from orchestra.providers.mock import MockProvider, MockScenario, MockStep, MockToolCallSpec
from orchestra.providers.openai_compat import OpenAICompatProvider
from orchestra.providers.token_counter import TokenCounter, count_tokens_sync

if TYPE_CHECKING:
    from pathlib import Path


def _sample_request(*, role: str = "developer", node_id: str = "N1") -> ChatRequest:
    return ChatRequest(
        request_id="req_test_01",
        model=c.DEFAULT_MODEL_ID,
        messages=(ChatMessage(role="user", content="Implement module x"),),
        tools=(
            ToolDefinition(
                name="submit_patch",
                description="Submit patch",
                parameters={"type": "object", "properties": {}},
            ),
        ),
        extra_body={
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": 8192,
        },
        role=role,
        node_id=node_id,
    )


def test_settings_load_defaults_and_create_runtime_dirs(tmp_path: Path) -> None:
    """Verify Settings initializes runtime directories, clamps temperature, and masks secrets."""
    runtime_dir = tmp_path / "rt"
    settings = load_settings(
        env_file=None,
        overrides={
            "runtime_dir": str(runtime_dir),
            "llm": {"min_temperature": 0.2, "temperature": 0.0},
            "key_pool": {"keys": "sk-alpha-123456, sk-beta-654321"},
            "telegram": {"bot_token": "123456:ABCDEF"},
        },
    )
    assert settings.paths.projects_dir.is_dir()
    assert settings.paths.talents_dir.is_dir()
    assert settings.paths.skills_dir.is_dir()
    assert settings.paths.cache_dir.is_dir()
    assert settings.llm.effective_temperature() == pytest.approx(0.2)
    assert len(settings.key_pool.keys) == 2

    public = settings.dump_public()
    dumped_str = json.dumps(public)
    assert "sk-alpha" not in dumped_str
    assert "123456:ABCDEF" not in dumped_str
    assert public["telegram"]["bot_token"] == "***REDACTED***"


def test_settings_russian_validation_errors_and_profiles(tmp_path: Path) -> None:
    """Verify invalid settings raise Russian ConfigurationError and load TOML profiles."""
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(
            env_file=None,
            overrides={"runtime_dir": str(tmp_path / "rt"), "llm": {"temperature": 5.0}},
        )
    assert "Недопустимое значение конфигурации ORCHESTRA" in str(exc_info.value)

    settings_empty = load_settings(
        env_file=None,
        overrides={"runtime_dir": str(tmp_path / "rt2"), "key_pool": {"keys": []}},
    )
    with pytest.raises(ConfigurationError) as live_exc:
        settings_empty.validate_for_live_inference()
    assert "пул API-ключей пуст" in str(live_exc.value)

    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "ci.toml").write_text(
        '[llm]\nprovider = "mock"\n[swarm]\nmax_concurrent_agents = 3\n',
        encoding="utf-8",
    )
    ci_settings = load_settings(
        env_file=None,
        profile_dir=profile_dir,
        overrides={"profile": "ci", "runtime_dir": str(tmp_path / "rt_ci")},
    )
    assert ci_settings.llm.provider == "mock"
    assert ci_settings.swarm.max_concurrent_agents == 3
    ci_settings.validate_for_live_inference()


def test_models_registry_and_token_counter() -> None:
    """Verify ModelsRegistry cost estimation and deterministic offline TokenCounter."""
    reg = ModelsRegistry(
        [
            ModelMetadata(
                model_id="paid/model",
                display_name="Paid Model",
                input_cost_per_1m_usd=2.0,
                output_cost_per_1m_usd=8.0,
                reasoning_cost_per_1m_usd=4.0,
            )
        ]
    )
    cost = reg.estimate_cost_usd(
        "paid/model",
        prompt_tokens=500_000,
        completion_tokens=250_000,
        reasoning_tokens=250_000,
    )
    assert cost == pytest.approx(4.0)

    counter = TokenCounter(allow_tiktoken=False)
    assert counter.count("") == 0
    assert counter.count("REQ N1 -> N2: CHECK CONTRACT") > 4
    assert count_tokens_sync("Привет, рой агентов!") > 3


class _FakeProbeTransport:
    def __init__(
        self,
        *,
        models: list[str],
        min_temp_accepted: float = 0.0,
        reasoning_field: str | None = "reasoning_content",
        network_fail: bool = False,
    ) -> None:
        self.models = models
        self.min_temp_accepted = min_temp_accepted
        self.reasoning_field = reasoning_field
        self.network_fail = network_fail
        self.probe_calls = 0

    async def list_models(self, *, api_key: str) -> list[str]:
        if self.network_fail:
            raise ProviderUnavailable("Connection refused")
        return self.models

    async def micro_probe(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.probe_calls += 1
        if temperature < self.min_temp_accepted:
            raise BadRequest("Invalid temperature: must be > 0")
        return {"reasoning_field": self.reasoning_field}


async def test_capability_prober_caching_binary_search_and_degradation(tmp_path: Path) -> None:
    """Verify CapabilityProber caches 24h, searches min_temperature, and degrades offline."""
    prober = CapabilityProber(cache_dir=tmp_path / "cache")

    missing_transport = _FakeProbeTransport(models=["other/model-1"])
    with pytest.raises(ModelNotFoundError):
        await prober.probe(missing_transport, model_id=c.DEFAULT_MODEL_ID, api_key="sk-test")

    temp_transport = _FakeProbeTransport(
        models=[c.DEFAULT_MODEL_ID],
        min_temp_accepted=0.2,
        reasoning_field="thinking",
    )
    caps = await prober.probe(temp_transport, model_id=c.DEFAULT_MODEL_ID, api_key="sk-test")
    assert caps.min_temperature >= 0.2
    assert caps.reasoning_field_name == "thinking"
    assert caps.capabilities_assumed is False

    # Second call hits disk cache without invoking transport again
    calls_before = temp_transport.probe_calls
    cached_caps = await prober.probe(temp_transport, model_id=c.DEFAULT_MODEL_ID, api_key="sk-test")
    assert temp_transport.probe_calls == calls_before
    assert cached_caps.min_temperature == caps.min_temperature

    # Network failure degrades gracefully with capabilities_assumed=True
    offline_transport = _FakeProbeTransport(models=[], network_fail=True)
    assumed = await prober.probe(
        offline_transport,
        model_id="unprobed/offline-model",
        api_key="sk-test",
    )
    assert assumed.capabilities_assumed is True


async def test_mock_provider_yaml_and_fault_modes(tmp_path: Path) -> None:
    """Verify MockProvider implements LLMProvider, YAML loading, and all fault modes."""
    yaml_path = tmp_path / "scenario.yaml"
    yaml_path.write_text(
        """
name: unit_scenario
steps:
  - role: developer
    node_id: N1
    max_uses: 1
    fault: rate_limit
    retry_after_s: 12
  - role: developer
    node_id: N1
    reasoning_chunks: ["step1 ", "step2"]
    content_chunks: ['{"ok":', ' true}']
""",
        encoding="utf-8",
    )
    scenario = MockScenario.from_yaml(yaml_path)
    provider = MockProvider(scenario)
    assert isinstance(provider, LLMProvider)

    req = _sample_request()
    with pytest.raises(RateLimited) as rl_exc:
        async for _ in provider.stream_chat(req, api_key="sk-mock-1"):
            pass
    assert rl_exc.value.retry_after_s == 12

    deltas = [d async for d in provider.stream_chat(req, api_key="sk-mock-1")]
    reasoning = "".join(d.text for d in deltas if isinstance(d, ReasoningDelta))
    content = "".join(d.text for d in deltas if isinstance(d, ContentDelta))
    assert reasoning == "step1 step2"
    assert content == '{"ok": true}'
    assert any(isinstance(d, UsageDelta) and d.total_tokens > 0 for d in deltas)
    assert any(isinstance(d, FinishDelta) and d.reason == "stop" for d in deltas)

    provider.add_step(
        MockStep(
            node_id="N_INT",
            reasoning_chunks=("partial-r",),
            content_chunks=("partial-c",),
            fault="stream_interrupted",
        )
    )
    with pytest.raises(StreamInterrupted) as int_exc:
        async for _ in provider.stream_chat(_sample_request(node_id="N_INT")):
            pass
    assert int_exc.value.partial_content == "partial-c"

    provider.add_step(
        MockStep(
            node_id="N_TOOL",
            tool_calls=(MockToolCallSpec(name="submit_patch", arguments='{"a":1}'),),
        )
    )
    tool_deltas = [
        d
        async for d in provider.stream_chat(_sample_request(node_id="N_TOOL"))
        if isinstance(d, ToolCallDelta)
    ]
    assert len(tool_deltas) == 1
    assert tool_deltas[0].name == "submit_patch"


def _sse_body(*events: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(ev)}\n\n" for ev in events]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


async def test_openai_compat_provider_streaming_and_error_matrix(tmp_path: Path) -> None:
    """Verify OpenAICompatProvider separates reasoning/content and maps HTTP error codes."""
    base_url = "https://mock-nvidia.local/v1"
    prober = CapabilityProber(cache_dir=tmp_path / "cache")
    provider = OpenAICompatProvider(
        base_url=base_url,
        default_model=c.DEFAULT_MODEL_ID,
        prober=prober,
    )
    assert compute_key_fingerprint("sk-secret-key") != "sk-secret-key"
    assert len(compute_key_fingerprint("sk-secret-key")) == 8

    sse_payload = _sse_body(
        {"id": "1", "object": "chat.completion.chunk", "choices": []},
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"reasoning_content": "THINK_A "}, "finish_reason": None}
            ],
        },
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": '{"res": 1}'}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 35,
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        },
    )

    with respx.mock(base_url=base_url, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_payload,
            )
        )
        deltas = [d async for d in provider.stream_chat(_sample_request(), api_key="sk-key-1")]

    assert [d.text for d in deltas if isinstance(d, ReasoningDelta)] == ["THINK_A "]
    assert [d.text for d in deltas if isinstance(d, ContentDelta)] == ['{"res": 1}']
    usage_deltas = [d for d in deltas if isinstance(d, UsageDelta)]
    assert usage_deltas[0].reasoning_tokens == 5

    # Verify HTTP status classification (429, 401, 400 overflow, 503, length, content_filter)
    cases: list[tuple[httpx.Response, type[Exception]]] = [
        (
            httpx.Response(
                429,
                headers={"Retry-After": "9"},
                json={"error": {"message": "Too many requests"}},
            ),
            RateLimited,
        ),
        (httpx.Response(401, json={"error": {"message": "Invalid key"}}), AuthFailed),
        (
            httpx.Response(400, json={"error": {"message": "maximum context length exceeded"}}),
            ContextOverflow,
        ),
        (httpx.Response(503, json={"error": {"message": "Upstream down"}}), ProviderUnavailable),
        (
            httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse_body(
                    {
                        "id": "2",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": {"content": "cut"}, "finish_reason": "length"}
                        ],
                    }
                ),
            ),
            TruncatedOutput,
        ),
        (
            httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=_sse_body(
                    {
                        "id": "3",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}],
                    }
                ),
            ),
            ContentFiltered,
        ),
    ]
    for resp, expected_exc in cases:
        with respx.mock(base_url=base_url) as router:
            router.post("/chat/completions").mock(return_value=resp)
            with pytest.raises(expected_exc):
                async for _ in provider.stream_chat(_sample_request(), api_key="sk-key-1"):
                    pass

    await provider.close()
