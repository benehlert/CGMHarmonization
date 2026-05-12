from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str


SUPPORTED_MODELS = (
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "o4-mini",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
)

DEFAULT_MODEL_MATRIX = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "o4-mini",
]
DEFAULT_CGM_MODEL = "gpt-5.4-mini"
DEFAULT_GENERAL_MODEL = "gpt-5.4"

_MODEL_PROVIDERS = {
    "gpt-5.4": "openai",
    "gpt-5.4-mini": "openai",
    "gpt-5.4-nano": "openai",
    "o4-mini": "openai",
    "claude-opus-4-7": "anthropic",
    "claude-sonnet-4-6": "anthropic",
    "claude-haiku-4-5-20251001": "anthropic",
    "gemini-3.1-pro-preview": "google",
    "gemini-3-flash-preview": "google",
    "gemini-3.1-flash-lite-preview": "google",
}

_ALIASES = {model.lower(): model for model in SUPPORTED_MODELS}
_ALIASES.update(
    {
        "gpt-5": "gpt-5.4",
        "gpt-5-mini": "gpt-5.4-mini",
        "gpt-5-nano": "gpt-5.4-nano",
        "gpt-5.4 mini": "gpt-5.4-mini",
        "gpt-5.4 nano": "gpt-5.4-nano",
        "gpt 5.4": "gpt-5.4",
        "gpt 5.4 mini": "gpt-5.4-mini",
        "gpt 5.4 nano": "gpt-5.4-nano",
        "mini": "gpt-5.4-mini",
        "nano": "gpt-5.4-nano",
        "claude opus 4.7": "claude-opus-4-7",
        "claude opus 4 7": "claude-opus-4-7",
        "claude-opus-4.7": "claude-opus-4-7",
        "opus 4.7": "claude-opus-4-7",
        "claude sonnet 4.6": "claude-sonnet-4-6",
        "claude sonnet 4 6": "claude-sonnet-4-6",
        "claude-sonnet-4.6": "claude-sonnet-4-6",
        "sonnet 4.6": "claude-sonnet-4-6",
        "claude haiku 4.5": "claude-haiku-4-5-20251001",
        "claude haiku 4.5 20251001": "claude-haiku-4-5-20251001",
        "claude haiku 4 5": "claude-haiku-4-5-20251001",
        "claude haiku 4 5 20251001": "claude-haiku-4-5-20251001",
        "claude-haiku-4.5": "claude-haiku-4-5-20251001",
        "claude-haiku-4.5-20251001": "claude-haiku-4-5-20251001",
        "claude-haiku-4-5": "claude-haiku-4-5-20251001",
        "haiku 4.5": "claude-haiku-4-5-20251001",
        "haiku 4.5 20251001": "claude-haiku-4-5-20251001",
        "gemini 3.1 pro": "gemini-3.1-pro-preview",
        "gemini-3.1-pro": "gemini-3.1-pro-preview",
        "gemini 3.1 pro preview": "gemini-3.1-pro-preview",
        "gemini 3 pro": "gemini-3.1-pro-preview",
        "gemini 3 flash": "gemini-3-flash-preview",
        "gemini-3-flash": "gemini-3-flash-preview",
        "gemini 3 flash preview": "gemini-3-flash-preview",
        "gemini 3.1 flash-lite": "gemini-3.1-flash-lite-preview",
        "gemini 3.1 flash lite": "gemini-3.1-flash-lite-preview",
        "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
        "gemini 3.1 flash-lite preview": "gemini-3.1-flash-lite-preview",
    }
)


def resolve_model_name(model_name: str) -> str:
    normalized = model_name.strip()
    if not normalized:
        raise ValueError("Model name cannot be empty.")
    return _ALIASES.get(normalized.lower(), normalized)


def resolve_model_spec(model_name: str) -> ModelSpec:
    resolved = resolve_model_name(model_name)
    provider = _MODEL_PROVIDERS.get(resolved)
    if provider is None:
        lowered = resolved.lower()
        if lowered.startswith("claude-"):
            provider = "anthropic"
        elif lowered.startswith("gemini-"):
            provider = "google"
        else:
            provider = "openai"
    return ModelSpec(provider=provider, model=resolved)


def _split_raw_model_string(raw_models: str) -> list[str]:
    stripped = raw_models.strip()
    if not stripped:
        return []
    if resolve_model_name(stripped) != stripped or stripped.lower() in _ALIASES:
        return [stripped]
    if "," in stripped:
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return [part for part in re.split(r"\s+", stripped) if part]


def parse_model_list(raw_models: str | Iterable[str] | None) -> List[str]:
    if raw_models is None:
        return list(DEFAULT_MODEL_MATRIX)
    if isinstance(raw_models, str):
        raw_values = _split_raw_model_string(raw_models)
    else:
        raw_values = [str(part).strip() for part in raw_models if str(part).strip()]

    if not raw_values:
        return list(DEFAULT_MODEL_MATRIX)

    deduped: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        resolved = resolve_model_name(value)
        if resolved not in seen:
            deduped.append(resolved)
            seen.add(resolved)
    return deduped


def model_slug(model_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", resolve_model_name(model_name).lower()).strip("-")
