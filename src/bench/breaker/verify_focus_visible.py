"""Are the three 2.4.7 defects really invisible? Measured with real Tab presses.

Two wrong ways to check this, both of which I used first:

  1. Reading `outlineStyle === 'none'` after applying `outline: none`. That is a
     tautology, not a test. It passed while the switch kept a perfectly visible
     focus indicator, because switch.css signals focus with padding,
     border-width and two background colours, and already sets outline:none
     itself.
  2. Calling `element.focus()` from JavaScript and diffing screenshots.
     Measured on the clean page, a programmatically focused input reports
     `outline-style: none` and the two frames come back byte-identical, while
     the same element reached by pressing Tab shows a 3px ring. Programmatic
     focus is not keyboard focus.

So this drives the real recorder: real Tab presses, real screenshots, the same
`focus_delta` the 2.4.7 check reads. A fixture is valid only when the
detector's own evidence says the defect is there.

Usage:  python breaker/verify_focus_visible.py <sandbox-id>
"""

from __future__ import annotations

import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import wb_env  # noqa: E402

wb_env.load_dotenv()
wb_env.use_certifi_bundle()

from agent.checks import INVISIBLE_BELOW  # noqa: E402
from agent.session import Session  # noqa: E402

BROKEN = "https://broken-app.vercel.app/2-4-7.html"
CLEAN = "https://ally-clean-app.vercel.app/"

#: Matched on accessible name, because the recorder derives its own selectors
#: and a class-based one would not line up.
#: label, name fragment, state, is a planted defect
TARGETS = [
    ("2.4.7 i1  name field", "full name", "loaded", True),
    ("2.4.7 i2  city field", "city", "dialog-open", True),
    ("2.4.7 i3  switch", "notifications", "loaded", True),
    ("control   email field", "email", "loaded", False),
]


def deltas(session: Session, url: str, run_id: str) -> dict:
    """focus_delta for every stop, keyed by (state, lowercased accessible name)."""
    out: dict = {}
    for state in ("loaded", "dialog-open"):
        rec = session.record(url, state, run_id=run_id)
        if not rec.state_reached:
            print(f"    [{state}] not reached: {rec.reach_note}")
            continue
        for stop in rec.stops:
            if stop.focus_delta is None or not stop.name:
                continue
            out[(state, stop.name.strip().lower())] = stop.focus_delta
    return out


def find(table: dict, state: str, fragment: str):
    for (st, name), value in table.items():
        if st == state and fragment in name:
            return value
    return None


def main() -> None:
    session = Session(sys.argv[1] if len(sys.argv) > 1
                      else os.environ.get("ALLY_SANDBOX"))
    print("recording the broken page ...")
    broken = deltas(session, BROKEN, "fv-broken")
    print("recording the clean page  ...")
    clean = deltas(session, CLEAN, "fv-clean")

    print(f"\n{'target':26s} {'broken':>11s} {'clean':>11s}  verdict"
          f"     (invisible below {INVISIBLE_BELOW})")
    print("-" * 90)
    bad = 0
    for label, fragment, state, planted in TARGETS:
        db, dc = find(broken, state, fragment), find(clean, state, fragment)
        fb = "not reached" if db is None else f"{db:.4f}"
        fc = "not reached" if dc is None else f"{dc:.4f}"
        if db is None or dc is None:
            verdict, ok = "NOT MEASURED", False
        elif planted:
            ok = db < INVISIBLE_BELOW <= dc
            verdict = ("defect present" if ok else
                       "STILL VISIBLE on broken" if db >= INVISIBLE_BELOW else
                       "invisible on clean too, so never a defect")
        else:
            ok = db >= INVISIBLE_BELOW and dc >= INVISIBLE_BELOW
            verdict = "visible on both, as expected" if ok else "CONTROL BROKE"
        bad += not ok
        print(f"{label:26s} {fb:>11s} {fc:>11s}  {verdict}")
    print("-" * 90)
    print("all three defects verified by pixel difference" if not bad
          else f"{bad} target(s) wrong -- fix before running the baseline")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
