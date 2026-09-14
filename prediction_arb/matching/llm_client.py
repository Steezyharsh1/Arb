"""LLM client construction + OpenAI-compatible chat completion (§19, §97).

This module is TRANSPORT ONLY. It turns a prompt into raw model text and
back; every decision about what that text means stays in
`matching/llm_matcher.py` — schema validation (§88), "malformed ⇒ no
match", contradiction checks, caching, and prompt/model versioning. An
LLM outage or a bad response is a transport error, which the matcher
converts into a conservative no-match (the pair goes to human REVIEW);
it is never retried into a match.

Configuration comes ONLY from the mode env file (`.env.paper` /
`.env.live`), never from code or chat:

    LLM_API_BASE         e.g. https://api.openai.com/v1
    LLM_API_KEY          the provider key (never logged, never committed)
    LLM_MODEL            e.g. gpt-4o-mini
    LLM_TIMEOUT_SECONDS  optional, default 60

Any OpenAI-compatible `/chat/completions` endpoint works. Setting none of
these is fine and conservative (no LLM ⇒ every ambiguous pair stays
REVIEW); setting SOME of them is operator error and refuses startup, so a
half-configured matcher can never be mistaken for a working one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from .llm_matcher import LLMMatcher

LLM_API_BASE_ENV = "LLM_API_BASE"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_MODEL_ENV = "LLM_MODEL"
LLM_TIMEOUT_ENV = "LLM_TIMEOUT_SECONDS"

_DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMConfigError(RuntimeError):
    """LLM matching is partially or wrongly configured; startup must
    refuse rather than silently run without semantic matching."""


class LLMTransportError(RuntimeError):
    """The LLM endpoint could not be consulted (unreachable, non-200,
    non-JSON, unexpected shape). The matcher turns this into no-match."""


@dataclass(frozen=True)
class LLMClientConfig:
    api_base: str
    api_key: str
    model: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS


def build_llm_config(env: dict[str, str]) -> Optional[LLMClientConfig]:
    """Build the client config from a mode env dict.

    Returns None when nothing is set (conservative no-LLM default), a
    config when everything required is set, and raises LLMConfigError on
    partial or malformed configuration — the one state that must never
    happen silently is "the operator thinks semantic matching is on but
    it isn't".
    """
    base = (env.get(LLM_API_BASE_ENV) or "").strip()
    key = (env.get(LLM_API_KEY_ENV) or "").strip()
    model = (env.get(LLM_MODEL_ENV) or "").strip()

    if not (base or key or model):
        return None

    missing = [name for name, value in (
        (LLM_API_BASE_ENV, base),
        (LLM_API_KEY_ENV, key),
        (LLM_MODEL_ENV, model),
    ) if not value]
    if missing:
        raise LLMConfigError(
            f"LLM matching is partially configured: {', '.join(missing)} "
            f"is missing. Set all of {LLM_API_BASE_ENV}, {LLM_API_KEY_ENV} "
            f"and {LLM_MODEL_ENV} in the mode env file — or none of them "
            f"to run without semantic matching (every ambiguous pair "
            f"stays REVIEW). See docs/API_SETUP.md.")

    if not base.startswith(("http://", "https://")):
        raise LLMConfigError(
            f"{LLM_API_BASE_ENV} must start with http:// or https:// "
            f"(got {base!r}).")

    timeout = _DEFAULT_TIMEOUT_SECONDS
    raw_timeout = (env.get(LLM_TIMEOUT_ENV) or "").strip()
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise LLMConfigError(
                f"{LLM_TIMEOUT_ENV} must be a number of seconds "
                f"(got {raw_timeout!r}).") from None
        if timeout <= 0:
            raise LLMConfigError(
                f"{LLM_TIMEOUT_ENV} must be positive (got {raw_timeout!r}).")

    return LLMClientConfig(api_base=base, api_key=key, model=model,
                           timeout_seconds=timeout)


async def chat_completion(config: LLMClientConfig, prompt: str,
                          transport: Optional[httpx.AsyncBaseTransport] = None,
                          ) -> str:
    """One OpenAI-compatible chat completion; returns the assistant text.

    Raises LLMTransportError on anything that is not a well-formed 200
    response — the matcher treats that as a conservative no-match (§19),
    never as a retry-into-match.
    """
    base = config.api_base.rstrip("/")
    url = base if base.endswith("/chat/completions") \
        else f"{base}/chat/completions"
    body = {
        "model": config.model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {config.api_key}"}

    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds,
                                     transport=transport) as client:
            response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMTransportError(
            f"LLM endpoint unreachable: "
            f"{exc.__class__.__name__}: {exc}") from exc

    if response.status_code != 200:
        raise LLMTransportError(
            f"LLM endpoint returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMTransportError(
            "LLM endpoint returned a non-JSON body") from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMTransportError(
            "LLM response has unexpected shape "
            "(missing choices[0].message.content)") from exc
    if not isinstance(content, str):
        raise LLMTransportError("LLM response content is not a string")
    return content


def build_llm_matcher(env: dict[str, str],
                      max_comparisons_per_cycle: Optional[int] = None,
                      transport: Optional[httpx.AsyncBaseTransport] = None,
                      ) -> tuple[LLMMatcher, str]:
    """Build the LLMMatcher for a run from the mode env.

    Returns (matcher, model_label). With no LLM configuration the matcher
    has client=None — the conservative default: every ambiguous pair ends
    in REVIEW and nothing is ever auto-APPROVED (§19). The model label
    feeds certificates (§20) so every approval records which model
    produced it.
    """
    config = build_llm_config(env)  # may raise LLMConfigError

    if config is None:
        return LLMMatcher(client=None,
                          max_comparisons_per_cycle=max_comparisons_per_cycle
                          ), "none"

    async def call(prompt: str) -> str:
        return await chat_completion(config, prompt, transport=transport)

    matcher = LLMMatcher(client=call, model_version=config.model,
                         max_comparisons_per_cycle=max_comparisons_per_cycle)
    return matcher, config.model
