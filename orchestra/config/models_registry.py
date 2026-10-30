"""Model registry with metadata, baseline token pricing, and default capability hints (OB-05).

Business logic must rely on probed ProviderCapabilities (PRV-04) rather than model name heuristics;
this registry stores pricing and baseline metadata used before or alongside live probing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from orchestra.config.constants import DEFAULT_MODEL_ID


class ModelMetadata(BaseModel):
    """Static metadata and token pricing for a registered LLM model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    display_name: str
    max_context_tokens: int = Field(default=131_072, ge=1_024)
    input_cost_per_1m_usd: float = Field(default=0.0, ge=0.0)
    output_cost_per_1m_usd: float = Field(default=0.0, ge=0.0)
    reasoning_cost_per_1m_usd: float = Field(default=0.0, ge=0.0)
    default_supports_thinking: bool = True
    default_supports_tools: bool = True


DEFAULT_MODELS: tuple[ModelMetadata, ...] = (
    ModelMetadata(
        model_id=DEFAULT_MODEL_ID,
        display_name="NVIDIA Nemotron 3.5 Lightning 30B A3B",
        max_context_tokens=131_072,
        input_cost_per_1m_usd=0.0,
        output_cost_per_1m_usd=0.0,
        reasoning_cost_per_1m_usd=0.0,
    ),
    ModelMetadata(
        model_id="z-ai/glm5",
        display_name="GLM Reasoning Model (NVIDIA Build)",
        max_context_tokens=131_072,
        input_cost_per_1m_usd=0.0,
        output_cost_per_1m_usd=0.0,
        reasoning_cost_per_1m_usd=0.0,
    ),
    ModelMetadata(
        model_id="mock/deterministic-v1",
        display_name="Deterministic Offline Mock Model",
        max_context_tokens=131_072,
        input_cost_per_1m_usd=0.0,
        output_cost_per_1m_usd=0.0,
        reasoning_cost_per_1m_usd=0.0,
    ),
)


class ModelsRegistry:
    """In-memory lookup registry for configured LLM models and cost estimation."""

    def __init__(self, models: list[ModelMetadata] | None = None) -> None:
        """Initialize the registry with built-in defaults plus optional custom entries."""
        self._models: dict[str, ModelMetadata] = {m.model_id: m for m in DEFAULT_MODELS}
        if models:
            for item in models:
                self._models[item.model_id] = item

    def register(self, metadata: ModelMetadata) -> None:
        """Register or update metadata for a model identifier."""
        self._models[metadata.model_id] = metadata

    def get(self, model_id: str) -> ModelMetadata:
        """Return registered metadata or a safe zero-cost fallback record."""
        if model_id in self._models:
            return self._models[model_id]
        return ModelMetadata(
            model_id=model_id,
            display_name=model_id,
            max_context_tokens=131_072,
        )

    def estimate_cost_usd(
        self,
        model_id: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        reasoning_tokens: int = 0,
    ) -> float:
        """Estimate USD cost for a single LLM invocation (OB-05)."""
        meta = self.get(model_id)
        prompt_cost = (max(0, prompt_tokens) / 1_000_000) * meta.input_cost_per_1m_usd
        comp_cost = (max(0, completion_tokens) / 1_000_000) * meta.output_cost_per_1m_usd
        reas_cost = (max(0, reasoning_tokens) / 1_000_000) * meta.reasoning_cost_per_1m_usd
        return round(prompt_cost + comp_cost + reas_cost, 8)

    def list_all(self) -> list[ModelMetadata]:
        """Return all registered model definitions sorted by model_id."""
        return [self._models[k] for k in sorted(self._models)]
