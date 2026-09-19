"""The patcher: findings in, find-and-replace pairs out, applied by our code.

Never send a file to a model and take a whole file back. A 2,231 line file came
back with 4,033 changed lines for a task that needed three attributes added.
The model returns a short piece of existing text and its replacement; this
module does the search and the substitution.

**Two counters that mean opposite things.** Keep them apart everywhere.

  Locate attempts. The text the model asked us to find is not in the file, or
  matches more than once. We reply with what is really there and it tries
  again. Three attempts, then `not_evaluated: could not locate the code`. Zero
  patches were applied, so this has no position on a patch-attempt axis.

  Patch attempts. The patch applied cleanly, we rebuilt and re-audited, and the
  finding is still open. The fix was wrong. Five attempts, then
  `not_evaluated: still failing after 5 patches`.

A finding can locate perfectly every time and never close.

**One copy of a patch exists, and it lives in a file.** The planner writes it,
the approval screen reads that file, the applier reads the same file. A
fingerprint is taken at write time and checked before applying, so the bytes
somebody approved are the bytes that land. AccessiFix compared a patch against
a freshly regenerated one from the same pure function, which is a check that
could never fail.

**Findings are grouped before anything is sent.** Twenty violations of the same
component are one request, not twenty. Independent requests mean the model has
no memory of the fix it just wrote, and A11YRepair documented three icons
getting two different techniques, which broke voice control. Coarse grouping
solved 59% and created 377 new problems; fine grouping solved 78% and created
172.
"""

from __future__ import annotations

import os
import re
import json
import time
import hashlib
import pathlib
from dataclasses import dataclass, field, asdict

from .recording import Result



def _op(fn):
    return fn


MAX_LOCATE_ATTEMPTS = 3
MAX_PATCH_ATTEMPTS = 5

from .claude import PATCHER_MODEL, structured  # noqa: E402

MODEL = PATCHER_MODEL
MAX_TOKENS = 32000


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

@dataclass
class Group:
    """Findings that should be fixed together: one component, one criterion."""

    criterion: str
    component: str
    findings: list[Result] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    #: The page these findings are on. The loop re-audits this page to decide
    #: whether the patch closed them, so findings from two pages never share a
    #: group even when they name the same component.
    page: str = ""

    @property
    def key(self) -> str:
        return f"{self.criterion}:{self.component}"


def component_of(selector: str) -> str:
    """The component a selector belongs to, for grouping.

    The nearest identified ancestor if there is one, otherwise the top of the
    path. Crude on purpose: the point is that instances of the same component
    travel together, not that the name is pretty.
    """
    if not selector:
        return "page"
    # The deepest identified ancestor names the component. Taking the first id
    # in the path, or the first path segment, put every element with no id in
    # the same "body" group, which defeats the point of grouping.
    ids = re.findall(r"#([A-Za-z0-9_\-]+)", selector)
    if ids:
        return f"#{ids[-1]}"
    parts = [p.strip() for p in selector.split(">") if p.strip()]
    return ">".join(parts[-2:]) if parts else "page"


def group_findings(results: list[Result]) -> list[Group]:
    """Group open failures so each one is sent exactly once.

    A finding carries every instance of its criterion it found, and the prompt
    asks for all of them to be fixed in one response with one technique. So a
    finding belongs to ONE group.

    Fanning a finding out across one group per target looked like finer
    grouping and was not: the first group's patch fixed all three instances,
    and the other two groups then spent their whole locate budget hunting for
    work that was already done, and were reported as "could not locate the
    code". Finer grouping than this needs the checks to emit one finding per
    instance, which is a change to the matrix, not to the patcher.
    """
    groups: dict[str, Group] = {}
    for r in results:
        if r.status != "failed":
            continue
        targets = list(r.targets or ("page",))
        comp = component_of(targets[0])
        key = f"{r.criterion}:{r.state}:{comp}:{r.page}"
        g = groups.setdefault(key, Group(criterion=r.criterion, component=comp,
                                         page=r.page))
        g.findings.append(r)
        for t in targets:
            if t not in g.targets:
                g.targets.append(t)
    return sorted(groups.values(), key=lambda g: (g.criterion, g.component))


# --------------------------------------------------------------------------
# The patch, as a file
# --------------------------------------------------------------------------

@dataclass
class Edit:
    """One find-and-replace. `find` must appear exactly once in the file."""

    path: str
    find: str
    replace: str


@dataclass
class PatchPlan:
    patch_id: str
    criterion: str
    component: str
    rationale: str
    edits: list[Edit]
    addresses: list[str] = field(default_factory=list)
    locate_attempts: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def safe_id(patch_id: str) -> str:
    """A patch id that is a legal filename everywhere.

    A component name like `section:nth-of-type(5)>div` carries a colon, and on
    Windows a colon in a path is the alternate-data-stream separator: the write
    silently landed on a stream of a file named `2-1-1-section` instead of
    creating the file. The patch was then not where anything expected it.
    """
    return _UNSAFE_ID.sub("-", patch_id).strip("-")[:120] or "patch"


def plan_path(root: pathlib.Path, run_id: str, patch_id: str) -> pathlib.Path:
    return root / "runs" / run_id / "patches" / f"{safe_id(patch_id)}.json"


def write_plan(root: pathlib.Path, run_id: str, plan: PatchPlan) -> tuple[pathlib.Path, str]:
    """Write the one copy of this patch, and fingerprint it.

    Nothing regenerates the patch after this. The approval screen and the
    applier both read this file.
    """
    path = plan_path(root, run_id, plan.patch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(plan.to_dict(), indent=2, sort_keys=True)
    path.write_text(body, encoding="utf-8")
    return path, hashlib.sha256(body.encode()).hexdigest()


def read_plan(path: pathlib.Path, expect_fingerprint: str | None = None) -> PatchPlan:
    """Read a patch back, refusing it if the bytes changed since approval."""
    body = path.read_text(encoding="utf-8")
    if expect_fingerprint is not None:
        actual = hashlib.sha256(body.encode()).hexdigest()
        if actual != expect_fingerprint:
            raise ValueError(
                f"{path.name} changed after it was approved "
                f"({actual[:12]} != {expect_fingerprint[:12]}). Refusing to apply: "
                "the bytes somebody looked at are not the bytes on disk."
            )
    data = json.loads(body)
    data["edits"] = [Edit(**e) for e in data["edits"]]
    return PatchPlan(**data)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

@dataclass
class ApplyOutcome:
    applied: bool
    reason: str = ""
    #: Per-edit: how many times `find` matched. 1 is required.
    matches: list[int] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)


def apply_plan(plan: PatchPlan, workdir: pathlib.Path) -> ApplyOutcome:
    """Apply every edit, or none of them.

    All-or-nothing: a half-applied patch leaves the tree in a state nobody
    planned and the re-audit would be measuring something that was never
    proposed.
    """
    staged: dict[pathlib.Path, str] = {}
    matches: list[int] = []

    for edit in plan.edits:
        target = workdir / edit.path
        if not target.exists():
            return ApplyOutcome(False, f"{edit.path} does not exist in the checkout")
        text = staged.get(target, target.read_text(encoding="utf-8"))
        count = text.count(edit.find)
        matches.append(count)
        if count == 0:
            return ApplyOutcome(False, f"no match in {edit.path}", matches)
        if count > 1:
            return ApplyOutcome(False, f"{count} matches in {edit.path}", matches)
        staged[target] = text.replace(edit.find, edit.replace, 1)

    # A model that will not repeat a long stretch of code sometimes writes a
    # note in its place, and if that note lands in the file the code it
    # replaced is gone. Counting rather than searching: a genuine helper whose
    # name looks like such a note gave AccessiFix a false alarm.
    NOTES = ("rest of the file unchanged", "... unchanged ...", "rest of code",
             "unchanged code here", "// ... existing")
    for target, new_text in staged.items():
        old_text = target.read_text(encoding="utf-8")
        for note in NOTES:
            if new_text.lower().count(note) > old_text.lower().count(note):
                return ApplyOutcome(
                    False,
                    f"the patch adds an elision note ({note!r}) to {target.name}, "
                    "which would delete the code it stands in for", matches)

    for target, new_text in staged.items():
        target.write_text(new_text, encoding="utf-8")
    return ApplyOutcome(True, "applied", matches,
                        [str(p.relative_to(workdir)) for p in staged])


# --------------------------------------------------------------------------
# The model call, with the locate retry loop
# --------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string"},
                    "replace": {"type": "string"},
                },
                "required": ["path", "find", "replace"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rationale", "edits"],
    "additionalProperties": False,
}

from .prompts import PATCHER_FIND_REPLACE

PROMPT = PATCHER_FIND_REPLACE


def request_edits(client, criterion: str, component: str, findings_text: str,
                  path: str, source: str, lessons: str = "",
                  retry_note: str = "", model: str = MODEL) -> dict:
    """One model call. `client` is ignored -- kept so call sites read the same.

    Opus, with thinking on and effort at xhigh, because this is the call that
    has to reproduce a block of source character for character. The previous
    model could not, and that is half of why no run before Accel ever produced
    a verified fix. The other half was being shown the wrong file, which
    whichfile.py now handles.
    """
    prompt = (PROMPT
              .replace("__CRITERION__", criterion)
              .replace("__COMPONENT__", component)
              .replace("__FINDINGS__", findings_text)
              .replace("__PATH__", path)
              .replace("__SOURCE__", source)
              .replace("__LESSONS__", lessons)
              .replace("__RETRY__", retry_note))
    return structured(prompt, SCHEMA, model=model,
                      max_tokens=MAX_TOKENS, effort="xhigh")


def resolve_find(source: str, find: str) -> str | None:
    """The real bytes in `source` that `find` means, or None.

    Exact first. Failing that, whitespace is treated as elastic: each run of
    whitespace in `find` matches any run of whitespace in the file. A model
    reproducing an indented JSX block gets the tokens right and the indentation
    wrong, and that is a transcription slip, not a different edit.

    Returns None unless the result is unambiguous. Uniqueness is what makes an
    edit safe to apply, and a fuzzier match must not buy itself an ambiguous
    one: a pattern that resolves to two places is refused exactly as a literal
    that appears twice is.
    """
    if source.count(find) == 1:
        return find
    stripped = find.strip()
    if stripped and source.count(stripped) == 1:
        return stripped
    if not stripped:
        return None

    parts = [re.escape(p) for p in stripped.split()]
    if not parts:
        return None
    pattern = r"\s+".join(parts)
    hits = list(re.finditer(pattern, source))
    if len(hits) != 1:
        return None
    return hits[0].group(0)


def locate_context(text: str, wanted: str, width: int = 400) -> str:
    """What is really in the file near where the model thought its text was.

    Sent back on a failed locate so the next attempt sees the real lines rather
    than guessing again.
    """
    head = wanted.strip().splitlines()[0][:40] if wanted.strip() else ""
    idx = text.find(head) if head else -1

    if idx < 0:
        # The model's text is not in the file at all, which is the case this
        # function exists for -- and it used to answer with the first 400
        # characters, which are the imports. Three retries each got the imports
        # back and each invented the code again. So look for the most
        # distinctive thing the model wrote instead: the words a human would
        # search for, longest first.
        words = sorted({w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{4,}", wanted)
                        if w not in ("className", "return", "export", "default",
                                     "import", "const", "aria", "onClick")},
                       key=len, reverse=True)
        for w in words[:8]:
            idx = text.find(w)
            if idx >= 0:
                break
        if idx < 0:
            return text[:width]

    start = max(0, idx - width // 2)
    return text[start:start + width]
