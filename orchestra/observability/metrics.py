"""Runtime telemetry, token cost estimator, and Prometheus text exporter (§29.2, OB-04..06)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from orchestra.config.models_registry import ModelMetadata, ModelsRegistry


class MetricsSnapshot(BaseModel):
    """Immutable telemetry snapshot for operator UI, post-mortem, and cost accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    llm_calls_total: int
    prompt_tokens_total: int
    completion_tokens_total: int
    reasoning_tokens_total: int
    estimated_cost_usd: float
    errors_429_total: int
    errors_5xx_total: int
    judge_verdicts_passed: int
    judge_verdicts_rework: int
    skill_executions_ok: int
    skill_executions_failed: int
    avg_latency_ms: float

    @property
    def judge_first_pass_rate(self) -> float:
        """Compute Independent Judge pass ratio across recorded verdicts."""
        total = self.judge_verdicts_passed + self.judge_verdicts_rework
        return round(self.judge_verdicts_passed / total, 4) if total > 0 else 1.0


class MetricsCollector:
    """In-memory telemetry aggregator tracking tokens, cost, judge verdicts, and latencies."""

    def __init__(self, models_registry: ModelsRegistry | None = None) -> None:
        """Initialize zeroed telemetry counters."""
        self._registry = models_registry or ModelsRegistry(
            models=[
                ModelMetadata(
                    model_id="deepseek-ai/deepseek-v3.1",
                    display_name="DeepSeek V3.1",
                    input_cost_per_1m_usd=0.27,
                    output_cost_per_1m_usd=1.10,
                    reasoning_cost_per_1m_usd=1.10,
                )
            ]
        )
        self._llm_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._reasoning_tokens = 0
        self._cost_usd = 0.0
        self._errors_429 = 0
        self._errors_5xx = 0
        self._judge_passed = 0
        self._judge_rework = 0
        self._skills_ok = 0
        self._skills_failed = 0
        self._latencies_ms: list[float] = []

    def record_llm_call(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """Record token usage, latency, and estimated USD cost for a single LLM invocation."""
        self._llm_calls += 1
        self._prompt_tokens += max(0, prompt_tokens)
        self._completion_tokens += max(0, completion_tokens)
        self._reasoning_tokens += max(0, reasoning_tokens)
        self._cost_usd += self._registry.estimate_cost_usd(
            model,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
            reasoning_tokens=max(0, reasoning_tokens),
        )
        if latency_ms >= 0:
            self._latencies_ms.append(latency_ms)

    def record_provider_error(self, *, status_code: int) -> None:
        """Increment 429 or 5xx error counters."""
        if status_code == 429:
            self._errors_429 += 1
        elif status_code >= 500:
            self._errors_5xx += 1

    def record_judge_verdict(self, *, passed: bool) -> None:
        """Record Independent Judge outcome."""
        if passed:
            self._judge_passed += 1
        else:
            self._judge_rework += 1

    def record_skill_execution(self, *, success: bool) -> None:
        """Record `/skills/` execution result."""
        if success:
            self._skills_ok += 1
        else:
            self._skills_failed += 1

    def snapshot(self) -> MetricsSnapshot:
        """Return immutable `MetricsSnapshot`."""
        avg_lat = (
            round(sum(self._latencies_ms) / len(self._latencies_ms), 2)
            if self._latencies_ms
            else 0.0
        )
        return MetricsSnapshot(
            llm_calls_total=self._llm_calls,
            prompt_tokens_total=self._prompt_tokens,
            completion_tokens_total=self._completion_tokens,
            reasoning_tokens_total=self._reasoning_tokens,
            estimated_cost_usd=round(self._cost_usd, 6),
            errors_429_total=self._errors_429,
            errors_5xx_total=self._errors_5xx,
            judge_verdicts_passed=self._judge_passed,
            judge_verdicts_rework=self._judge_rework,
            skill_executions_ok=self._skills_ok,
            skill_executions_failed=self._skills_failed,
            avg_latency_ms=avg_lat,
        )

    def export_prometheus_text(self) -> str:
        """Render OpenMetrics / Prometheus exposition text for scraping."""
        s = self.snapshot()
        return (
            "\n".join(
                [
                    f"orchestra_llm_calls_total {s.llm_calls_total}",
                    f"orchestra_prompt_tokens_total {s.prompt_tokens_total}",
                    f"orchestra_completion_tokens_total {s.completion_tokens_total}",
                    f"orchestra_reasoning_tokens_total {s.reasoning_tokens_total}",
                    f"orchestra_estimated_cost_usd {s.estimated_cost_usd}",
                    f"orchestra_errors_429_total {s.errors_429_total}",
                    f"orchestra_errors_5xx_total {s.errors_5xx_total}",
                    f"orchestra_judge_first_pass_rate {s.judge_first_pass_rate}",
                ]
            )
            + "\n"
        )
