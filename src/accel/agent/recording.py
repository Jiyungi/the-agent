"""The recording: what one Tab run through one page state produced.

Every one of the five checks reads this and nothing else. It is the single
piece of evidence in the system, which is why `evidence_refs` can be resolved
against it deterministically (SCOPE rule 5.2).

Three things the shape enforces:

  * **A result has three cases, not two.** `not_evaluated` carries a reason and
    the type will not let you construct it without one. A check that could not
    run must say so rather than being recorded as a pass.
  * **Screenshots are refs, never bytes.** `Stop.screenshot` holds what
    `ally.storage.save_screenshot` returned. The 1GB Weave cap is one benchmark
    run away if bytes ever reach an op input.
  * **Stop 0 exists.** The element focused when the state was entered, before
    any Tab. The evidence for a keyboard trap is "focus was here, Tab was
    pressed, focus is still here", and stop 0 is half of that comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

Status = Literal["passed", "failed", "not_evaluated"]


@dataclass(frozen=True)
class Census:
    """How many elements each of the four outcomes happened to.

    Modelled on axe, which returns four lists per rule -- passes, violations,
    incomplete, inapplicable -- rather than one verdict. `incomplete` means it
    looked and could not decide; `inapplicable` means there was no matching
    content to look at. Our `not_evaluated` collapsed those two into one, and
    from outside both were indistinguishable from a pass.

    This is what makes a miss visible. "found 2 of 3" says nothing about whether
    the third was examined and cleared or never looked at; "examined 40, failed
    2, passed 37, could not decide about 1" does.

    `examined` is the sum of failed, passed and undecided, and the type enforces
    that. `inapplicable` and `excluded` sit outside it, because neither was
    examined: the first had nothing to check, the second was skipped on purpose.
    """

    examined: int = 0
    failed: int = 0
    passed: int = 0
    #: Looked at and could not decide. axe calls this incomplete.
    undecided: int = 0
    #: No matching content on the element to check.
    inapplicable: int = 0
    #: Deliberately skipped. A silent exclusion is a miss nobody can see, so the
    #: reasons travel with the count.
    excluded: int = 0
    exclusion_note: str = ""

    def __post_init__(self) -> None:
        total = self.failed + self.passed + self.undecided
        if self.examined != total:
            raise ValueError(
                f"census examined={self.examined} but failed+passed+undecided="
                f"{total}. Every examined element lands in exactly one of the "
                "three, or the counts are decoration rather than a measurement."
            )

    @property
    def line(self) -> str:
        """The counts as one readable line."""
        bits = [f"examined {self.examined}", f"failed {self.failed}",
                f"passed {self.passed}"]
        if self.undecided:
            bits.append(f"could not decide about {self.undecided}")
        if self.inapplicable:
            bits.append(f"nothing to check on {self.inapplicable}")
        if self.excluded:
            bits.append(f"excluded {self.excluded}"
                        + (f" ({self.exclusion_note})" if self.exclusion_note else ""))
        return ", ".join(bits)


def exclusion_summary(rec) -> tuple[int, str]:
    """The recording's exclusions as a count and a reason breakdown."""
    ex = getattr(rec, "excluded", None) or []
    if not ex:
        return 0, ""
    counts: dict[str, int] = {}
    for e in ex:
        label = {"managed": "roving tabindex",
                 "inside-control": "inside a larger control",
                 "delegating-container": "delegating container",
                 "not-visible": "not visible",
                 "opted-out": 'explicit tabindex="-1"',
                 "stale": "gone or changed by the end of the run"}.get(e.rule, e.rule)
        counts[label] = counts.get(label, 0) + 1
    note = ", ".join(f"{n} {label}" for label, n in sorted(counts.items()))
    return len(ex), note


@dataclass(frozen=True)
class Result:
    """One criterion's verdict on one state.

    Build with the constructors below, never directly: `not_evaluated` without
    a reason is the bug this whole type exists to prevent.
    """

    criterion: str
    state: str
    status: Status
    #: Required when status is not_evaluated. Never set otherwise.
    reason: str | None = None
    #: Refs into the recording that prove a failure. Required when failed.
    evidence_refs: tuple[str, ...] = ()
    #: One sentence for the report.
    summary: str = ""
    #: Which planted-defect-shaped thing was found, for the scorer to match on.
    targets: tuple[str, ...] = ()
    #: Element-level outcome counts. See Census.
    census: Census = field(default_factory=Census)
    #: The page this verdict is about. Empty on a single-page audit, where the
    #: audit's own URL is the only answer. It exists so the fix loop can
    #: re-audit the page a finding came from: a defect on /audit-trail was
    #: being reported and then abandoned, because the loop only ever re-visited
    #: the entry page and could not confirm a fix anywhere else.
    page: str = ""

    def __post_init__(self) -> None:
        if self.status == "not_evaluated" and not self.reason:
            raise ValueError(
                f"{self.criterion}: not_evaluated requires a reason. An unexplained "
                "not_evaluated is indistinguishable from a pass, which is the "
                "failure this type exists to prevent."
            )
        if self.status != "not_evaluated" and self.reason:
            raise ValueError(f"{self.criterion}: reason belongs only on not_evaluated")
        if self.status == "failed" and not self.evidence_refs:
            raise ValueError(
                f"{self.criterion}: a failure must cite evidence. Without a ref there "
                "is nothing for the resolution check to verify against."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def passed(criterion: str, state: str, summary: str = "",
           census: Census | None = None) -> Result:
    return Result(criterion=criterion, state=state, status="passed", summary=summary,
                  census=census or Census())


def failed(criterion: str, state: str, evidence_refs: tuple[str, ...] | list[str],
           summary: str, targets: tuple[str, ...] | list[str] = (),
           census: Census | None = None) -> Result:
    return Result(criterion=criterion, state=state, status="failed",
                  evidence_refs=tuple(evidence_refs), summary=summary,
                  targets=tuple(targets), census=census or Census())


def not_evaluated(criterion: str, state: str, reason: str,
                  census: Census | None = None) -> Result:
    return Result(criterion=criterion, state=state, status="not_evaluated",
                  reason=reason, census=census or Census())


# --------------------------------------------------------------------------
# The recording
# --------------------------------------------------------------------------

@dataclass
class Stop:
    """One focus position. Index 0 is the state-entry stop, before any Tab."""

    index: int
    tag: str
    role: str | None
    #: Computed by the accessibility tree, never read off the element. On the
    #: clean app the naive `innerText || value || aria-label` disagreed with the
    #: tree on 7 of 10 stops: labelled inputs came back empty and a switch
    #: returned its value as its name.
    name: str | None
    #: Document coordinates, not viewport. The page scrolls as focus moves, so
    #: viewport y is non-monotonic and reading order derived from it is wrong.
    x: int
    y: int
    w: int
    h: int
    selector: str
    #: Viewport coordinates. Document coords above are what reading order is
    #: derived from; these are what a screenshot crop must use, because a
    #: screenshot is viewport-sized and the page scrolls as focus moves.
    vx: int = 0
    vy: int = 0
    #: What storage.save_screenshot returned. A path or a URL. Never bytes.
    screenshot: str | None = None
    #: Is the focused element the one painted at its own centre? (2.4.11)
    obscured_by: str | None = None
    #: Pixel difference against the previous frame, cropped to this element.
    focus_delta: float | None = None
    #: What this element is, and what it sits inside. See browser.anch.
    #: {"self": ["input", "#email", ".city_input"], "within": ["#email_item"]}
    anchors: dict = field(default_factory=dict)
    #: The parent element's selector, used to group stops into layout blocks
    #: before reading order is derived. See checks.reading_order.
    parent: str | None = None

    @property
    def cite(self) -> str:
        """The citation form. `evidence_refs` entries look exactly like this.

        NOT named `ref`. Weave's `get_ref(obj)` is `getattr(obj, "ref", None)`,
        so a `ref` property returning a string makes Weave treat this object as
        already-saved and then call `ref.project` on a str. Every trace holding
        a Recording raised AttributeError inside _save_nested_objects and was
        dropped, which is why no stage before 2026-09-12 has a trace record.
        The collision is silent: weave.init succeeds and the ops still run.
        """
        return f"stop {self.index}"


@dataclass
class Candidate:
    """An element that looks interactive, from any of the three 2.1.1 sources."""

    selector: str
    tag: str
    role: str | None
    name: str | None
    x: int
    y: int
    w: int
    h: int
    focusable: bool
    #: "tree", "dom-query" or "listeners"
    sources: tuple[str, ...] = ()
    #: See Stop.anchors.
    anchors: dict = field(default_factory=dict)

    @property
    def cite(self) -> str:
        """See Stop.cite for why this is not called `ref`."""
        return f"candidate {self.selector}"


@dataclass
class Exclusion:
    """An element the candidate scan deliberately skipped, and why.

    A silent exclusion is a miss nobody can see. Both of these were added to
    cut 2.1.1 false positives on the control page, and the cost was never
    reported anywhere: the number of candidates simply went down. Recording
    them means an exclusion has to be argued for rather than forgotten.
    """

    selector: str
    tag: str
    role: str | None
    #: "managed" (roving tabindex inside a composite widget), "inside-control"
    #: (part of a larger control), or "delegating-container".
    rule: str
    reason: str
    source: str = ""


@dataclass
class Recording:
    """One Tab run through one state."""

    url: str
    state: str
    #: False when the reach step did not change the DOM. Every criterion for
    #: this state is then not_evaluated with that reason, rather than being
    #: judged against a state we never actually entered.
    state_reached: bool
    reach_note: str
    stops: list[Stop] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    #: Every element the scan skipped, with the rule that skipped it.
    excluded: list[Exclusion] = field(default_factory=list)
    #: True when the Tab loop hit its cap instead of wrapping.
    truncated: bool = False
    viewport: tuple[int, int] = (1024, 740)
    page_height: int = 0
    #: How many focusable elements the page has, counted in the browser. The
    #: denominator for coverage: see checks.coverage.
    focusable_total: int = 0
    #: What the consent-dialog dismissal clicked, or "" if there was nothing.
    consent_note: str = ""
    #: How many elements say they open a menu or a dialog. The job uses these to
    #: decide whether to tab those states, instead of asking the user.
    menu_triggers: int = 0
    dialog_triggers: int = 0
    #: Did the page load? nav_error is CDP's own navigation failure, http_status
    #: what the browser recorded, text_length whether anything rendered. See
    #: Recording.loaded_note.
    nav_error: str = ""
    http_status: int = 0
    text_length: int = 0
    title: str = ""

    @property
    def loaded_note(self) -> str:
        """Why this page is not the page, or "" when it looks real.

        Ally audited Chrome's own network error page once and reported four
        passes on it: readyState was "complete", and the Reload and Back buttons
        were two perfectly reachable controls. A verdict about a page we never
        reached is worse than no verdict, so this is checked before any check
        runs.
        """
        if self.nav_error:
            return f"the browser could not open it: {self.nav_error}"
        if self.http_status and not (200 <= self.http_status < 400):
            return f"the server answered {self.http_status}"
        if self.text_length < 120:
            # `if self.text_length and ...` used to guard this, so a page that
            # rendered zero characters -- the worst case there is -- was waved
            # through as if it had loaded. A re-audit of a page that never
            # rendered then reported two findings closed, because a blank page
            # fails no check.
            return (f"only {self.text_length} characters rendered, which is a "
                    "block page or an error, not a page to audit")
        return ""

    # -- evidence resolution (SCOPE rule 5.2) ------------------------------

    def resolve(self, ref: str) -> Stop | Candidate | None:
        """Look up a cited ref. Returns None when the model invented it.

        Deterministic, no model involved. This is the check that does most of
        the work of keeping unsupported findings out of the report.
        """
        ref = (ref or "").strip()
        if ref.startswith("stop "):
            try:
                index = int(ref[len("stop "):])
            except ValueError:
                return None
            for stop in self.stops:
                if stop.index == index:
                    return stop
            return None
        if ref.startswith("candidate "):
            want = ref[len("candidate "):]
            for c in self.candidates:
                if c.selector == want:
                    return c
        return None

    def unresolved(self, refs) -> list[str]:
        """Which of these refs point at nothing. Empty means all resolved."""
        return [r for r in refs if self.resolve(r) is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state,
            "state_reached": self.state_reached,
            "reach_note": self.reach_note,
            "truncated": self.truncated,
            "viewport": list(self.viewport),
            "page_height": self.page_height,
            "stops": [asdict(s) for s in self.stops],
            "candidates": [asdict(c) for c in self.candidates],
            "excluded": [asdict(e) for e in self.excluded],
            "focusable_total": self.focusable_total,
            "consent_note": self.consent_note,
            "menu_triggers": self.menu_triggers,
            "dialog_triggers": self.dialog_triggers,
            "nav_error": self.nav_error,
            "http_status": self.http_status,
            "text_length": self.text_length,
            "title": self.title,
        }
