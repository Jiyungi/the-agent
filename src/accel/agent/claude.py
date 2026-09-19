"""The one place a model is called. Anthropic, nowhere else in the codebase.

Two models, chosen for two different jobs:

  * **Opus writes the patches.** Editing source is where the previous model
    failed hardest -- it had to copy a block of JSX back character for
    character and could not, which is half of why no run ever produced a fix.
  * **Sonnet judges focus order.** That call is a yes/no reading-comprehension
    question over two short lists. Opus is not better at it and costs 2.5x
    more, on a call made four times per page per run.

`structured` returns a dict validated against a JSON schema by the API itself,
so a caller never parses a maybe-JSON string. The previous version had to
handle a truncated object mid-parse and report it as a budget failure rather
than a model failure; `max_tokens` is generous here for the same reason, and
the caller is told plainly when the cap was the thing that stopped it.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

#: Writes source edits.
PATCHER_MODEL = "claude-opus-5"
#: Decides whether a focus-order difference actually harms a keyboard user.
JUDGE_MODEL = "claude-sonnet-5"

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    """One client for the process. Reads ANTHROPIC_API_KEY from the environment."""
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=key)
    return _client


class ModelRefused(RuntimeError):
    """The request was declined. Distinct from a transport or parse failure."""


def structured(prompt: str, schema: dict, *, model: str,
               max_tokens: int = 16000, effort: str = "high",
               thinking: bool = True) -> dict[str, Any]:
    """One call, one JSON object back, shaped by `schema`.

    Streaming, because a patch over a large component with thinking on can run
    past the SDK's non-streaming HTTP timeout. `get_final_message` gives the
    whole response once it lands, so the caller sees no difference.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": schema},
                          "effort": effort},
    }
    if thinking:
        kwargs["thinking"] = {"type": "adaptive"}

    with client().messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()

    if msg.stop_reason == "refusal":
        raise ModelRefused("the model declined this request")
    if msg.stop_reason == "max_tokens":
        raise ValueError(
            f"the response was cut off at max_tokens={max_tokens}; ask for a "
            "smaller edit rather than the whole element")

    import json
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text.strip())
