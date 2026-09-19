"""The one model call in the audit: does a focus-order difference matter?

Claude Sonnet. The question is a short reading-comprehension call over two
lists of stops -- Opus is no more accurate on it and costs 2.5x more, four
times per page per run.

Two things this file will not let you get wrong:

  * **`evidence_refs` is required on a failure, in the schema and in the
    prompt.** With only `status` required, `{"status": "failed", "reason": ...}`
    is schema-valid and useless, and every model returned exactly that until
    the prompt demanded the refs explicitly. The caller still verifies each ref
    resolves to a real stop; that check is deterministic code and does most of
    the work.

  * **A judge that cannot answer says so.** Any failure here becomes
    `not_evaluated` with the reason attached, never a guess. A wrong verdict is
    worse than no verdict, because a verdict is what a finding is built on.

The reading order this judge is shown is derived from element geometry, and on
a two-column layout that derivation is wrong -- it interleaves the columns by
height. On ikea.com the judge described that difference accurately and called
it harmful, reasoning correctly from a premise that was false. The prompt now
says so explicitly, which is the cheap half of the fix.
"""

from __future__ import annotations

from .claude import JUDGE_MODEL, structured
from .prompts import FOCUS_ORDER_JUDGE

MODEL = JUDGE_MODEL

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["passed", "failed", "not_evaluated"]},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["status", "evidence_refs", "reason", "summary"],
    "additionalProperties": False,
}

PROMPT = FOCUS_ORDER_JUDGE


def make_judge(model: str = MODEL):
    """Return a judge callable for `checks.check_focus_order`."""

    def judge(rec, tab_order, expected) -> dict | None:
        by_index = {s.index: s for s in rec.stops}

        def rows(order):
            out = []
            for i in order:
                s = by_index.get(i)
                if s is None:
                    continue
                out.append(f"  stop {i}: <{s.tag.lower()}> "
                           f"{(s.name or '(no accessible name)')[:44]!r} "
                           f"at x={s.x} y={s.y}")
            return "\n".join(out)

        # replace, not .format(): the prompt shows JSON objects and str.format
        # reads their braces as fields.
        prompt = (PROMPT.replace("__STATE__", rec.state)
                        .replace("__TAB_ROWS__", rows(tab_order))
                        .replace("__READING_ROWS__", rows(expected)))
        try:
            out = structured(prompt, SCHEMA, model=model,
                             max_tokens=4000, effort="medium")
        except Exception as exc:
            return {"status": "not_evaluated",
                    "reason": f"the focus-order judge failed: {type(exc).__name__}: "
                              f"{str(exc)[:120]}"}

        # A pass or an unevaluated verdict has no refs to carry, and the schema
        # requires the key on every response, so drop the empty ones rather
        # than handing the caller [] to interpret.
        if out.get("status") != "failed":
            out.pop("evidence_refs", None)
        return out

    return judge
