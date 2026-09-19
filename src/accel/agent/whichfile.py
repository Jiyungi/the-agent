"""Which source file draws the element that failed.

This is the file the patcher is shown, and getting it wrong is what killed
every run before Accel existed. The old rule was *page -> file*: the finding is
on `/verify`, so show the model `VerifyPage.jsx`. That is right only when the
failing element is written in the page's own file.

On BitEstate it was not. The failing element was the question-mark button,
reported as `span>button`. `VerifyPage.jsx` contains no `<button>` at all -- it
contains this:

    <HelpTooltip>The file stays in your browser...</HelpTooltip>

and the button is three lines inside `HelpTooltip.jsx`:

    <button className="help-trigger" type="button" aria-label={label}>

So the model was shown a file the element is not in, asked to copy the
element's exact text out of it, and could only invent something. The search for
that invented text matched nothing, three times, and the run ended with

    could not locate the code after 3 attempts ... the find text matched 0 times

on **19 consecutive runs**. Not a transcription failure. A file-selection
failure that looked like one.

The fix needs no new data. `Stop.anchors` and `Candidate.anchors` already carry
what the element is and what it sits inside, collected in the browser at record
time:

    {"self": ["button", ".help-trigger"], "within": ["span", ".help-tip", ...]}

A class name written in the DOM is written in the source that produced it, so
`grep -rl "help-trigger" src/` lands on `HelpTooltip.jsx` directly. The id and
class tokens are the query; the repository answers it.

Ordering, and why it is what it is:

  * **Own classes before ancestor classes.** The element's own class names the
    component that draws it. An ancestor's class names its parent, which is the
    file we are trying to move away from.
  * **A token in one file beats a token in six.** `.btn` is in every component
    and tells us nothing; `.help-trigger` is in one and tells us everything.
    Matches are ranked by how few files the token appears in.
  * **The page's own file is the fallback, never an error.** When no token
    resolves -- an element styled only by tag, a class generated at build time
    -- the old behaviour is what is left, and the caller is told which rule
    answered so a bad lookup is visible in the run rather than silent.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

#: Class and id tokens that appear all over any codebase. A token that matches
#: half the repository is not evidence about which file to open.
TOO_COMMON = {
    "btn", "button", "link", "active", "open", "show", "hide", "hidden",
    "container", "wrapper", "row", "col", "item", "card", "box", "flex",
    "grid", "text", "title", "header", "footer", "main", "content", "page",
    "small", "large", "primary", "secondary", "disabled", "selected", "icon",
    "label", "input", "field", "form", "list", "menu", "nav", "modal",
    "root", "app", "left", "right", "center", "top", "bottom",
}

#: A CSS-Modules or styled-components class looks like `Button_root__x7f2k`.
#: The hash changes on every build, so it is in the DOM and never in the source.
_BUILD_HASH = re.compile(r"__[A-Za-z0-9]{5,}$|^[a-z]{3,}-[0-9a-f]{6,}$")

#: Where source lives. Checked in order; the first that exists is searched.
SOURCE_DIRS = ("src", "app", "components", "pages", "lib", ".")

#: Files worth opening. A match inside a lockfile or a build output is noise.
SOURCE_EXTS = (".jsx", ".tsx", ".js", ".ts", ".vue", ".svelte", ".html", ".astro")

_SKIP_DIRS = ("node_modules", "dist", "build", ".next", ".git", "coverage",
              ".parcel-cache", "out", "public")


@dataclass
class Located:
    """Which file, and which rule answered."""

    path: str
    #: "own-class", "own-id", "ancestor-class", "page-fallback"
    rule: str
    #: The token that found it, for the run log.
    token: str = ""
    #: How many files that token appears in. 1 is the confident case.
    files_matched: int = 0

    @property
    def confident(self) -> bool:
        return self.rule != "page-fallback" and self.files_matched == 1

    def note(self) -> str:
        if self.rule == "page-fallback":
            return f"{self.path} (no element token resolved; fell back to the page's file)"
        return (f"{self.path} (matched {self.token!r} via {self.rule}, "
                f"{self.files_matched} file{'' if self.files_matched == 1 else 's'})")


def _usable(token: str) -> bool:
    """Is this token worth searching for?"""
    if len(token) < 4:
        return False
    if token.lower() in TOO_COMMON:
        return False
    if _BUILD_HASH.search(token):
        return False
    return True


def tokens_from_anchors(anchors: dict) -> tuple[list[str], list[str]]:
    """(own tokens, ancestor tokens), most specific first, junk removed.

    `anchors` is the shape `browser.anch` produces: every token carries its
    sigil, so `.help-trigger` is a class, `#email` an id, `[role=switch]` a
    role, and a bare `button` the tag name. Only classes and ids are searchable
    -- a tag name matches every file and a role is usually written as a JSX
    prop, not the literal string.
    """
    def pick(seq):
        out = []
        for raw in seq or []:
            if raw.startswith("."):
                t = raw[1:]
            elif raw.startswith("#"):
                t = raw[1:]
            else:
                continue          # tag names and [role=...] are not evidence
            if _usable(t) and t not in out:
                out.append(t)
        # Longest first: a longer class name is a more specific one.
        return sorted(out, key=len, reverse=True)

    return pick(anchors.get("self")), pick(anchors.get("within"))


def anchors_for_targets(audit, targets: list[str]) -> list[dict]:
    """The anchors of every recorded element named in `targets`.

    A target is a selector string as the check reported it. The same element is
    usually in several recordings -- one per page state -- and they agree, so
    the first match is taken.
    """
    found: list[dict] = []
    seen: set[str] = set()
    for rec in getattr(audit, "recordings", {}).values():
        for el in list(getattr(rec, "stops", [])) + list(getattr(rec, "candidates", [])):
            sel = getattr(el, "selector", "")
            if sel in targets and sel not in seen and getattr(el, "anchors", None):
                seen.add(sel)
                found.append(el.anchors)
    return found


def _grep_files(session, checkout: str, token: str) -> list[str]:
    """Source files under `checkout` containing `token`, newline separated.

    Runs in the sandbox, because that is where the clone is. `grep -rl` with an
    explicit include list rather than a find/xargs pipeline: fewer moving parts
    and one round trip.
    """
    includes = " ".join(f"--include='*{e}'" for e in SOURCE_EXTS)
    excludes = " ".join(f"--exclude-dir='{d}'" for d in _SKIP_DIRS)
    dirs = " ".join(f"{checkout}/{d}" for d in SOURCE_DIRS[:-1])
    cmd = (f"grep -rl {includes} {excludes} -F -- {shlex.quote(token)} "
           f"{dirs} 2>/dev/null | head -20")
    try:
        out = session.sb.process.exec(cmd, timeout=60).result or ""
    except Exception:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def locate(session, checkout: str, audit, targets: list[str],
           page_file: str) -> Located:
    """The file to hand the patcher, and the rule that chose it.

    `page_file` is the old answer -- the file that draws the page -- and stays
    as the fallback. It is never wrong in a way that breaks anything; it is
    just wrong about where the element lives, which is the bug this exists for.
    """
    anchor_sets = anchors_for_targets(audit, targets)
    if not anchor_sets:
        return Located(page_file, "page-fallback")

    own: list[str] = []
    ancestor: list[str] = []
    for a in anchor_sets:
        o, w = tokens_from_anchors(a)
        own += [t for t in o if t not in own]
        ancestor += [t for t in w if t not in ancestor]

    checkout = checkout.rstrip("/")

    # Own tokens first, then ancestors. Within each, a token that resolves to
    # exactly one file wins immediately; otherwise remember the narrowest and
    # keep looking for a unique one.
    best: Located | None = None
    for rule, toks in (("own-class", own), ("ancestor-class", ancestor)):
        for t in toks:
            hits = _grep_files(session, checkout, t)
            if not hits:
                continue
            rel = hits[0][len(checkout) + 1:] if hits[0].startswith(checkout) else hits[0]
            if len(hits) == 1:
                return Located(rel, rule, t, 1)
            if best is None or len(hits) < best.files_matched:
                best = Located(rel, rule, t, len(hits))

    if best is not None:
        return best
    return Located(page_file, "page-fallback")
