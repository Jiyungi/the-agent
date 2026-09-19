"""The fifteen defects as a gate, failing on any drop in recall.

Rule 1 runs constantly, so every false positive is seen the day it appears.
The fifteen defects ran rarely, so a miss was almost never seen. That asymmetry
is how the scoreboard came to read 8 of 15 while the checks were finding 14, and
how three exclusions went in with nobody able to say what they cost.

axe-core runs thousands of fixture cases on every pull request, half of which
must produce a finding and half of which must produce nothing. This is the same
idea at our scale: twenty-four frozen recordings, fifteen defects that must be
found, and a clean page that must stay silent.

**Why it is fast enough to run on every change.** Recording is the expensive
part -- a sandbox, a browser, four states per page. The checks are pure
functions of a Recording. So the recordings are frozen under
`tests/fixtures/recordings/` and replayed, which takes under a second and needs
no sandbox. Re-freeze them with `--freeze` after a deliberate recorder or
fixture change, and say so in the commit.

**What this therefore cannot catch:** a regression in the recorder itself. The
corpus is fixed, so a recorder that stops finding stops will still score 15/15
here. That needs a periodic full `scorer/score.py` run, and the determinism
check in the session tests.

Usage:
    python tests/test_recall_gate.py
    python tests/test_recall_gate.py --no-judge   # reports 2.4.3 as unverified
"""

from __future__ import annotations

import sys
import json
import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import wb_env  # noqa: E402

wb_env.load_dotenv()
wb_env.use_certifi_bundle()

from agent import checks  # noqa: E402
from agent.recording import Candidate, Exclusion, Recording, Stop  # noqa: E402
from scorer.score import CRITERIA, PAGE_OF, anchor_index, load_manifest, matches  # noqa: E402
from scorer.score import LEGACY_USES  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "recordings"
FLOOR = ROOT / "tests" / "fixtures" / "recall_floor.json"


def rebuild(raw: dict) -> Recording:
    """A Recording from its own `to_dict`, so the corpus needs no sandbox."""
    rec = Recording(url=raw.get("url", ""), state=raw.get("state", ""),
                    state_reached=raw.get("state_reached", True),
                    reach_note=raw.get("reach_note", ""),
                    truncated=raw.get("truncated", False),
                    page_height=raw.get("page_height", 0))
    for s in raw.get("stops", ()):
        rec.stops.append(Stop(**s))
    for c in raw.get("candidates", ()):
        c = dict(c)
        c["sources"] = tuple(c.get("sources") or ())
        rec.candidates.append(Candidate(**c))
    for e in raw.get("excluded", ()):
        rec.excluded.append(Exclusion(**e))
    return rec


def load_corpus() -> dict[str, list[Recording]]:
    """page -> its recordings, in a stable order."""
    if not CORPUS.exists():
        raise SystemExit(f"no corpus at {CORPUS}; re-freeze from a scored baseline")
    out: dict[str, list[Recording]] = {}
    for path in sorted(CORPUS.glob("*__*.json")):
        page = path.name.split("__")[0]
        out.setdefault(page, []).append(
            rebuild(json.loads(path.read_text(encoding="utf-8"))))
    return out


def run_page(recs: list[Recording], judge) -> list:
    """Every check against every state of one page."""
    results = []
    for rec in recs:
        for criterion, fn in checks.CHECKS.items():
            with checks.criterion_tag(criterion, rec.state, rec.url):
                r = fn(rec) if criterion != "2.4.3" else fn(rec, judge=judge)
            results.append(r)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-judge", action="store_true",
                    help="skip the model call; 2.4.3 is then reported unverified "
                         "and the gate fails, per verification rule 7")
    ap.add_argument("--freeze", action="store_true",
                    help="rewrite the recall floor from this run's numbers")
    args = ap.parse_args()

    judge = None
    if args.no_judge:
        print("!! JUDGE SKIPPED. 2.4.3 is NOT verified by this run, so the gate")
        print("!! cannot pass. Rule 7: a skip is reported, never silent.")
        print()
    else:
        from agent.judge import make_judge
        judge = make_judge()

    corpus = load_corpus()
    planted = load_manifest()
    print(f"{sum(len(v) for v in corpus.values())} frozen recordings across "
          f"{len(corpus)} pages\n")

    rows, clean_fp = {}, []
    for crit in CRITERIA:
        page = PAGE_OF[crit]
        if page not in corpus:
            print(f"  {crit}: no recordings for {page}, cannot gate")
            return 1
        results = run_page(corpus[page], judge)
        run = {"findings": [r.to_dict() for r in results],
               "recordings": {r.state: r.to_dict() for r in corpus[page]}}
        anchors = anchor_index(run)

        mine = [r for r in results if r.criterion == crit]
        reported: list[str] = []
        for r in mine:
            if r.status == "failed":
                reported.extend(r.targets)
        reported = list(dict.fromkeys(reported))

        defects = planted.get(crit, [])
        found = [d for d in defects if any(matches(t, d, anchors) for t in reported)]
        tp = [t for t in reported if any(matches(t, d, anchors) for d in defects)]
        fp = [t for t in reported if t not in tp]
        ne = [r for r in mine if r.status == "not_evaluated"]

        ex = sum(r.census.examined for r in mine)
        und = sum(r.census.undecided for r in mine)
        inap = sum(r.census.inapplicable for r in mine)
        rows[crit] = {"found": len(found), "planted": len(defects),
                      "false_positives": len(fp), "fp_targets": fp,
                      "not_evaluated": len(ne), "examined": ex,
                      "undecided": und, "inapplicable": inap,
                      "missed": [f"{d['region']} ({d['selector']})"
                                 for d in defects if d not in found]}

    # The clean page must stay silent. Half the corpus producing nothing is what
    # stops a gate being satisfied by a check that fires on everything.
    if "clean" in corpus:
        for r in run_page(corpus["clean"], judge):
            if r.status == "failed":
                clean_fp.append((r.criterion, r.state, r.targets))

    print(f"{'criterion':10s} {'recall':>7s} {'examined':>9s} {'failed':>7s} "
          f"{'undecided':>10s} {'nothing':>8s} {'false pos':>10s}")
    print("-" * 72)
    for crit, v in rows.items():
        print(f"{crit:10s} {v['found']}/{v['planted']:<5d} {v['examined']:>9d} "
              f"{v['found']:>7d} {v['undecided']:>10d} {v['inapplicable']:>8d} "
              f"{v['false_positives']:>10d}")
    print("-" * 72)
    total_found = sum(v["found"] for v in rows.values())
    total_planted = sum(v["planted"] for v in rows.values())
    total_fp = sum(v["false_positives"] for v in rows.values())
    print(f"{'':10s} {total_found}/{total_planted:<5d} "
          f"{sum(v['examined'] for v in rows.values()):>9d} {'':>7s} "
          f"{sum(v['undecided'] for v in rows.values()):>10d} "
          f"{sum(v['inapplicable'] for v in rows.values()):>8d} {total_fp:>10d}")

    if args.freeze:
        FLOOR.write_text(json.dumps(
            {"recall": {c: v["found"] for c, v in rows.items()},
             "max_false_positives": {c: v["false_positives"] for c, v in rows.items()},
             "note": "Written by --freeze. Lowering a number here is a deliberate "
                     "act and belongs in the commit message with its reason."},
            indent=2), encoding="utf-8")
        print(f"\nfroze the floor to {FLOOR}")
        return 0

    if not FLOOR.exists():
        print(f"\nno floor recorded at {FLOOR}; run with --freeze once to set it")
        return 1
    floor = json.loads(FLOOR.read_text(encoding="utf-8"))

    failures = []
    for crit, v in rows.items():
        want = floor["recall"].get(crit, 0)
        if v["found"] < want:
            failures.append(f"{crit} recall fell from {want} to {v['found']} "
                            f"(missed: {', '.join(v['missed']) or 'unknown'})")
        cap = floor["max_false_positives"].get(crit, 0)
        if v["false_positives"] > cap:
            failures.append(f"{crit} false positives rose from {cap} to "
                            f"{v['false_positives']}: {', '.join(v['fp_targets'])}")

    for crit, state, targets in clean_fp:
        failures.append(f"{crit} fired on the CLEAN page in {state}: "
                        f"{', '.join(targets)}")

    # Every false positive stays named even when it is within the cap. A wrong
    # element cited inside a correct finding is a precision defect, and it does
    # not get to disappear into a passing row.
    named = [(c, t) for c, v in rows.items() for t in v["fp_targets"]]
    if named:
        print(f"\nfalse positives, all within cap unless listed as a failure below:")
        for crit, target in named:
            print(f"  {crit:8s} {target}")

    if LEGACY_USES:
        failures.append(f"{len(set(LEGACY_USES))} target(s) had no anchors, so they "
                        "were matched as strings. Re-freeze the corpus.")

    if args.no_judge:
        failures.append("the judge was skipped, so 2.4.3 is unverified")

    if failures:
        print(f"\nFAIL: {len(failures)} regression(s).")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nPASS: {total_found}/{total_planted} recall held, {total_fp} false "
          f"positive(s) within cap, clean page silent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
