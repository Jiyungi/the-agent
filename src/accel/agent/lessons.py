"""The lessons table: one row per patch outcome, queried before the next patch.

A table and a query. No training, no embeddings, no vector store. SQLite,
through the database seam, because moving to Postgres later should be a
connection string.

    criterion | element shape | fix applied | did it close | what the re-audit said

Written after every patch outcome, whether it closed or not. A fix that did not
work is more useful than one that did: it is the only thing that stops the next
attempt repeating it.

**The retrieved row ids go into the Weave trace for that patch call.** That is
what makes the loop provable rather than claimed. Without it, "patch attempts
per closed finding fell" is a line on a chart with no mechanism attached to it,
and a falling line can just as easily mean the later findings were easier.

The first instance of a criterion has nothing to retrieve. That is the point:
it is what the third instance is compared against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from accel.server import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  run_id        TEXT    NOT NULL,
  criterion     TEXT    NOT NULL,
  -- What the damaged element looked like, in the terms a later prompt can
  -- match on: tag, role, whether it was focusable, the shape of the defect.
  element_shape TEXT    NOT NULL,
  component     TEXT    NOT NULL,
  -- The technique, short enough to read in a prompt: "div -> button",
  -- "added tabindex=0", "removed outline:none override".
  fix_applied   TEXT    NOT NULL,
  -- The exact edit, for the cases where the shape is not enough.
  find_text     TEXT,
  replace_text  TEXT,
  closed        INTEGER NOT NULL,
  -- What the re-audit reported when it did not close. Empty when it closed.
  reaudit_said  TEXT    NOT NULL DEFAULT '',
  patch_attempt INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS lessons_criterion ON lessons (criterion);
"""


@dataclass
class Lesson:
    criterion: str
    element_shape: str
    component: str
    fix_applied: str
    closed: bool
    reaudit_said: str = ""
    find_text: str = ""
    replace_text: str = ""
    patch_attempt: int = 1


class Lessons:
    """Read and write the table. The only thing the fix loop knows about it."""

    def __init__(self, run_id: str, limit: int = 4) -> None:
        self.run_id = run_id
        self.limit = limit
        db.script(SCHEMA)

    # -- writing ----------------------------------------------------------

    def record(self, lesson: Lesson) -> int:
        return db.execute(
            "INSERT INTO lessons (run_id, criterion, element_shape, component, "
            "fix_applied, find_text, replace_text, closed, reaudit_said, patch_attempt) "
            "VALUES (:run_id, :criterion, :shape, :component, :fix, :find, :repl, "
            ":closed, :said, :attempt)",
            {"run_id": self.run_id, "criterion": lesson.criterion,
             "shape": lesson.element_shape, "component": lesson.component,
             "fix": lesson.fix_applied, "find": lesson.find_text[:400],
             "repl": lesson.replace_text[:400], "closed": int(lesson.closed),
             "said": lesson.reaudit_said[:400], "attempt": lesson.patch_attempt})

    # -- reading ----------------------------------------------------------

    def recall(self, group) -> tuple[list[dict], str]:
        """Prior cases for this criterion and a similar element.

        Returns (rows, prompt_text). The caller puts the row ids into the Weave
        trace for the patch call, which is what turns a falling line into
        something with a mechanism behind it.

        Ordered so a fix that WORKED on a similar element comes first, then
        anything that failed on this criterion: knowing what not to try again is
        most of the value, and a failed row read as a suggestion would be worse
        than no row at all, so the prompt labels each one.
        """
        shape = group_shape(group)
        rows = db.query_all(
            "SELECT id, criterion, element_shape, component, fix_applied, closed, "
            "reaudit_said, patch_attempt FROM lessons "
            "WHERE criterion = :criterion "
            "ORDER BY (component = :component) DESC, "
            "         (element_shape = :shape) DESC, "
            "         closed DESC, id DESC "
            "LIMIT :limit",
            {"criterion": group.criterion, "component": group.component,
             "shape": shape, "limit": self.limit})
        if not rows:
            return [], ""

        worked = [r for r in rows if r["closed"]]
        failed = [r for r in rows if not r["closed"]]
        parts = ["Prior attempts on this criterion, from earlier patches in this "
                 "run and previous runs. These are records of what happened, not "
                 "instructions.\n"]
        for r in worked:
            parts.append(f"  WORKED  [{r['id']}] on {r['element_shape']} "
                         f"in {r['component']}: {r['fix_applied']}")
        for r in failed:
            parts.append(f"  FAILED  [{r['id']}] on {r['element_shape']} "
                         f"in {r['component']}: {r['fix_applied']}\n"
                         f"          the re-audit then said: {r['reaudit_said'][:120]}")
        parts.append("\nUse the technique that worked. Do not repeat one that "
                     "failed on the same shape.\n")
        return rows, "\n".join(parts) + "\n"


def group_shape(group) -> str:
    """A short description of the element a group is about, for matching.

    Deliberately coarse. The point is that the third `div acting as a button`
    retrieves the first two, not that the description is precise.
    """
    bits = []
    for f in group.findings:
        s = (f.summary or "").lower()
        if "not focusable" in s or "never reaches" in s:
            bits.append("clickable but not focusable")
        if "stayed on the same element" in s:
            bits.append("focus repeats on the element")
        if "no visible change" in s:
            bits.append("no focus indicator")
        if "drawn over" in s:
            bits.append("obscured by another element")
        if "reading order" in s:
            bits.append("tab order differs from reading order")
    return "; ".join(dict.fromkeys(bits)) or "unknown"


def describe_fix(plan) -> str:
    """One short line naming the technique a patch used."""
    if not plan.edits:
        return "no edits"
    e = plan.edits[0]
    find, repl = e.find.strip(), e.replace.strip()

    def tag(text: str) -> str:
        return text[1:text.find(" ")] if " " in text[:40] else text[1:40].strip("<>")

    if find.startswith("<") and repl.startswith("<"):
        a, b = tag(find), tag(repl)
        if a != b:
            return f"{a} -> {b}"
    for attr in ("tabindex", "role", "outline", "order", "position", "aria-"):
        if attr in repl and attr not in find:
            return f"added {attr}"
        if attr in find and attr not in repl:
            return f"removed {attr}"
    return (plan.rationale or "edit")[:70]
