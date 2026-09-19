"""Up to four sandboxes, one page each, audited in isolation and in parallel.

The original plan, and the right one. A site is a set of pages; each page gets
its own sandbox, its own Chromium and its own desktop to watch. Nothing is
shared, so one page that hangs or crashes a browser cannot affect another, and
the wall-clock cost of a five-page site is roughly the cost of its slowest page
rather than the sum of all five.

Four is the cap, and it is a quota decision rather than a technical one: the
plan allows a handful of sandboxes and each one costs start-up time.

The first sandbox is reused from `ALLY_SANDBOX` when it is set, because a warm
sandbox starts a run in seconds where a cold one takes a minute.
"""

from __future__ import annotations

import os
import sys
import pathlib
import threading
import contextlib
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))



MAX_SANDBOXES = 4


@dataclass
class Lane:
    """One page, one sandbox, one browser, one desktop."""

    index: int
    url: str
    #: For a single-page flow: the registered state name to reach, and the label
    #: of the control that reaches it.
    state: str = ""
    label: str = ""
    sandbox_id: str = ""
    watch_url: str = ""
    #: "waiting" | "running" | "done" | "failed" | "skipped"
    status: str = "waiting"
    note: str = ""
    findings: list = field(default_factory=list)
    recordings: dict = field(default_factory=dict)
    axe: dict = field(default_factory=dict)
    source: str = ""
    #: What this lane actually rendered. Two routes that render the same screen
    #: get the same signature: BitEstate serves Home at both "/" and "/home",
    #: and auditing it twice would double-count every finding on it.
    signature: str = ""
    #: Set when another lane rendered this first.
    duplicate_of: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index, "url": self.url, "label": self.label,
            "sandbox_id": self.sandbox_id,
            "watch_url": self.watch_url, "status": self.status, "note": self.note,
            "source": self.source,
            "failed": sum(1 for f in self.findings if f.get("status") == "failed"),
            "checks": len(self.findings),
            "duplicate_of": self.duplicate_of,
        }


def _session_for(index: int, reuse: str | None):
    """A session for lane `index`: the warm sandbox first, then fresh ones."""
    from accel.agent.session import Session

    if index == 0 and reuse:
        return Session(reuse), reuse
    s = Session(None)                       # Session(None) creates a sandbox
    sid = getattr(getattr(s, "sb", None), "id", "") or ""
    return s, sid


def run_lanes(lanes: list[Lane], states: list[str], judge,
              say, tag: dict | None = None) -> list[Lane]:
    """Drive every lane at once, each in its own sandbox. Blocks until all end.

    `say(lane_index, message, level)` is called as things happen, so the caller
    can stream progress without this module knowing what a UI is.
    """
    from accel.agent.audit import Audit

    reuse = os.environ.get("ALLY_SANDBOX")
    threads: list[threading.Thread] = []

    def drive(lane: Lane) -> None:
        lane.status = "running"
        session = None
        # A lane runs in its own thread, so its checks cannot be children of
        # the run's trace. Tagging them is the next best thing: every call a
        # lane makes carries the job it belongs to and the page it was looking
        # at, so a run's work can still be collected in one view.
        ctx = contextlib.nullcontext()
        with ctx:
            _drive(lane)

    def _drive(lane: Lane) -> None:
        session = None
        try:
            session, sid = _session_for(lane.index, reuse)
            lane.sandbox_id = sid
            # Only after the desktop answers: the URL of a port nothing is
            # listening on renders a 502 body inside the panel, and an iframe
            # that loaded one never retries.
            try:
                if session.start_desktop():
                    lane.watch_url = session.watch_url()
                else:
                    say(lane.index, "no desktop on this sandbox; the audit "
                        "runs but there is nothing to watch", "warn")
            except Exception:
                pass
            say(lane.index, "sandbox ready, "
                + (f"opening {lane.url} then pressing {lane.label!r}"
                   if lane.label else f"opening {lane.url}"), "info")

            lane_states = [lane.state] if lane.state else states
            audit = Audit(lane.url, sandbox_id=sid or None, states=lane_states,
                          run_id=f"lane-{lane.index}", judge=judge)
            audit.session = session
            results = list(audit.run())

            rec = audit.recordings.get(lane_states[0]) if lane_states else None
            bad = rec.loaded_note if rec is not None else "nothing was recorded"
            if bad:
                lane.status = "skipped"
                lane.note = bad
                say(lane.index, f"skipped: {bad}", "warn")
                return

            lane.findings = [r.to_dict() for r in results]
            lane.recordings = {s: r.to_dict() for s, r in audit.recordings.items()}
            lane.axe = {"ran": audit.axe.ran if audit.axe else False,
                        "version": audit.axe.version if audit.axe else "",
                        "violations": audit.axe.violations if audit.axe else []}
            stops = (rec.stops if rec is not None else []) or []
            lane.signature = "|".join([
                (rec.title if rec is not None else ""),
                str(len(stops)),
                ",".join(getattr(x, "selector", "") for x in stops[:8]),
            ])
            lane.status = "done"
            failed = sum(1 for f in lane.findings if f["status"] == "failed")
            say(lane.index, f"{failed} finding(s) on "
                + (lane.label or lane.url), "info")
        except Exception as exc:
            lane.status = "failed"
            lane.note = f"{type(exc).__name__}: {str(exc)[:160]}"
            say(lane.index, lane.note, "error")
        finally:
            # Session.close() only deletes a sandbox it created: it keeps an
            # _owned flag and leaves a reused one alone. So this is safe to call
            # on every lane, including the one that reuses ALLY_SANDBOX.
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    for lane in lanes[:MAX_SANDBOXES]:
        t = threading.Thread(target=drive, args=(lane,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return lanes
