"""The two prompts Accel runs.

Plain strings. The Weave `StringPrompt` wrapper is gone with the rest of the
Weave instrumentation; these are the objects `judge.py` and `patcher.py`
import, so there is still exactly one copy of each prompt in the system.

Substitution is `.replace()` on `__PLACEHOLDER__`, never `str.format`. Both
prompts show JSON objects to the model and `format` reads their braces as
field references.
"""

from __future__ import annotations


FOCUS_ORDER_JUDGE = """You judge WCAG 2.4.3 Focus Order on one page state.

Below are the elements the keyboard reached, in the order Tab reached them, and
the order a sighted person would read them, derived from their positions on the
page. Positions are document coordinates in CSS pixels.

A difference between the two orders is not automatically a failure. Decide
whether this particular difference would disadvantage someone using a keyboard.

Read the two orders as a person looking at the page would. If the positions
describe side-by-side columns, a reader finishes one column before starting the
next -- they do not zig-zag between them by height. A "reading order" that
interleaves two columns is an artifact of sorting by vertical position, not a
real reading order, and a tab order that disagrees with it is not a failure.

Reply as JSON only.
  matches, or the difference is harmless -> {"status": "passed", "summary": "..."}
  the difference disadvantages a keyboard user ->
      {"status": "failed", "evidence_refs": ["stop 3", "stop 5"], "summary": "..."}
  you cannot tell from this evidence ->
      {"status": "not_evaluated", "reason": "..."}

evidence_refs is REQUIRED on a failed verdict. Each entry names a stop from the
list below, written exactly as "stop N", and must be one of the stops where the
order actually goes wrong. Do not use reason on a failed verdict.

State: __STATE__

Tab order reached these stops, in this order:
__TAB_ROWS__

Reading order derived from position would be:
__READING_ROWS__
"""


PATCHER_FIND_REPLACE = """You fix accessibility defects by returning find-and-replace pairs.

Criterion: __CRITERION__
Component: __COMPONENT__

What the audit found:
__FINDINGS__

The source file is __PATH__. Here it is:
--- BEGIN __PATH__ ---
__SOURCE__
--- END __PATH__ ---

__LESSONS__
Return JSON only:
{"rationale": "one sentence", "edits": [{"path": "...", "find": "...", "replace": "..."}]}

Rules for `find`:
  It must appear EXACTLY ONCE in the file. Include enough surrounding text to
  be unique; a short fragment that appears twice will be rejected.
  Copy it character for character from the source above, including whitespace.
  Never write an elision note such as "rest of the file unchanged": anything
  you omit from `replace` is deleted.

Rules for the fix itself:
  Do not remove an interactive element to make a finding go away. Deleting a
  button, link or input closes the finding and removes the feature, which is a
  worse outcome than the defect. Removing an ATTRIBUTE is fine -- stripping
  `outline: none`, or a positive `tabindex`, is often the correct fix. The test
  is whether a person loses something they could click.

  If the only way you can see to satisfy the criterion is to remove an
  interactive element, return an empty `edits` array and say so in `rationale`.

Fix every listed instance in one response, using the same technique for each.
Mixing techniques across instances of one component is how a fix breaks voice
control: the visible text and the announced name stop matching.
__RETRY__"""


#: Every prompt this system runs.
ALL = {
    "focus-order-judge": FOCUS_ORDER_JUDGE,
    "patcher-find-replace": PATCHER_FIND_REPLACE,
}
