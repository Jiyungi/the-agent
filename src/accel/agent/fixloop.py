"""The fix loop: patch, rebuild, re-audit, count what closed and what appeared.

A finding is closed only when the same check, on the same state, on the rebuilt
page, no longer reports it. Never when the patch merely applied. AccessiFix
closed findings on a successful compile and reported zero failures while eight
were still open.

Verification rule 1 applies here more than anywhere, because the patcher's
whole job is making findings disappear, which is also what a broken patcher
looks like. Three things are therefore measured after every patch:

  * the finding it targeted is gone from the re-audit
  * the re-audit's other findings are counted, so a patch that closes one and
    creates two is reported as closing one and creating two
  * the clean page is re-checked and must still return zero

A patch that closes nine and creates four has closed five.
"""

from __future__ import annotations

import contextlib
import dataclasses

import os
import json
import time
import pathlib
import shutil
from dataclasses import dataclass, field

from accel.server import db
from .patcher import (MAX_LOCATE_ATTEMPTS, MAX_PATCH_ATTEMPTS, ApplyOutcome,
                      Edit, Group, PatchPlan, apply_plan, group_findings,
                      locate_context, read_plan, request_edits, resolve_find,
                      write_plan)
from .lessons import Lesson, describe_fix, group_shape
from .recording import Result, not_evaluated



def _op(fn):
    return fn


#: Serves the patched build, and resolves an unknown path to index.html the
#: way every single-page app is deployed. Without it a re-audit of
#: /audit-trail gets a 404 page, which fails no check and therefore reads as
#: a successful fix.
SPA_SERVER = """
import http.server, os, sys, urllib.parse

ROOT = os.path.abspath(sys.argv[1])


class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = urllib.parse.urlparse(path).path
        full = os.path.join(ROOT, p.lstrip('/'))
        if os.path.isdir(full):
            idx = os.path.join(full, 'index.html')
            if os.path.exists(idx):
                return idx
        if os.path.exists(full):
            return full
        return os.path.join(ROOT, 'index.html')

    def log_message(self, *a):
        pass


http.server.HTTPServer(('127.0.0.1', 3000), H).serve_forever()
"""


@dataclass
class FixOutcome:
    """What happened to one group of findings."""

    criterion: str
    component: str
    #: "closed", "still_failing", "could_not_locate", "rejected"
    status: str
    locate_attempts: int = 0
    patch_attempts: int = 0
    closed: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    reason: str = ""
    patch_files: list[str] = field(default_factory=list)
    lesson_ids: list[int] = field(default_factory=list)

    def as_result(self, state: str) -> Result | None:
        """The three-state record for a group that never closed."""
        if self.status == "could_not_locate":
            return not_evaluated(self.criterion, state,
                                 f"could not locate the code after "
                                 f"{self.locate_attempts} attempts")
        if self.status == "still_failing":
            return not_evaluated(self.criterion, state,
                                 f"still failing after {self.patch_attempts} patches")
        return None


class FixLoop:
    """Drives patch -> rebuild -> re-audit for one audit's findings."""

    def __init__(self, audit, workdir: pathlib.Path, serve_root: str,
                 client=None, lessons=None, approve=None, build: str = "") -> None:
        self.audit = audit
        self.workdir = pathlib.Path(workdir)
        self.serve_root = serve_root.rstrip("/")
        #: A shell command that rebuilds the checkout, run before each re-audit.
        #: Empty for a plain HTML page, which is served as it stands.
        self.build = build
        #: Why the last re-audit could not be scored, if it could not be.
        self.blocked = ""
        self.client = client
        #: Stage 4 plugs the lessons table in here. Absent, the prompt carries
        #: no prior cases and the first instance of a criterion has nothing to
        #: retrieve, which is the point of the number that moves.
        self.lessons = lessons
        #: Called with the patch file path. Returns True to apply. Absent, the
        #: loop applies without a gate, which is only correct in the benchmark.
        self.approve = approve
        self.outcomes: list[FixOutcome] = []
        self.root = pathlib.Path(os.environ.get("ALLY_ROOT", "."))

    # -- one group --------------------------------------------------------

    def fix_group(self, group: Group, source_path: str) -> FixOutcome:
        """Retrieve prior cases, then patch inside a trace tagged with their ids.

        The ids are the mechanism. Without them "patch attempts per closed
        finding fell" is a line on a chart, and a falling line can equally mean
        the later findings were easier. With them a reader can open a patch call
        and see which earlier cases went into the prompt that produced it.

        This used to call `weave.attributes({...}).__enter__()` and never exit
        it. An unbalanced context manager either leaks the attributes into
        unrelated later calls or applies to nothing, depending on the
        implementation, and either way the one claim it exists to support could
        not be checked. It is a `with` block now, wrapping every patch attempt
        for this group.
        """
        lesson_rows, lesson_text = [], ""
        if self.lessons is not None:
            lesson_rows, lesson_text = self.lessons.recall(group)

        ctx = contextlib.nullcontext()
        with ctx:
            return self._fix_group(group, source_path, lesson_rows, lesson_text)

    def _fix_group(self, group: Group, source_path: str, lesson_rows: list,
                   lesson_text: str) -> FixOutcome:
        target = self.workdir / source_path
        findings_text = "\n".join(
            f"  - {f.summary}\n    evidence: {', '.join(f.evidence_refs[:4])}"
            for f in group.findings)

        outcome = FixOutcome(criterion=group.criterion, component=group.component,
                             status="could_not_locate",
                             lesson_ids=[r["id"] for r in lesson_rows])

        patch_attempts = 0
        retry_note = ""
        # Every attempt starts from the file as it was. Without this, attempt
        # five was patching a file four broken patches deep, and the diff that
        # reached the pull request was the wreckage of all of them.
        original = target.read_text(encoding="utf-8")

        while patch_attempts < MAX_PATCH_ATTEMPTS:
            # Each patch attempt gets its OWN budget of three locate attempts.
            # Sharing one cumulative counter let patch retries eat the locate
            # budget: a group whose code was located perfectly three times, and
            # whose three fixes simply did not work, was reported as "could not
            # locate the code". That is exactly the conflation the two counters
            # exist to prevent.
            plan, used, last = self._locate(group, source_path, findings_text,
                                            lesson_text, retry_note)
            outcome.locate_attempts += used
            if plan is None:
                # Nothing left to find is not a failure to find something. One
                # patch often closes every instance of a criterion at once --
                # three divs becoming three buttons in one edit -- and the next
                # attempt then reports "the find text matched 0 times". Reporting
                # that group as could_not_locate while it had already closed
                # three findings is the counter contradicting itself, and it
                # reached the screen looking like a broken run.
                if outcome.closed:
                    outcome.status = "closed"
                    outcome.reason = ("closed by an earlier patch in this group; "
                                      "nothing remained to locate")
                    return outcome
                outcome.status = "could_not_locate"
                outcome.reason = (f"could not locate the code after "
                                  f"{MAX_LOCATE_ATTEMPTS} attempts on patch attempt "
                                  f"{patch_attempts + 1}" + (f": {last}" if last else ""))
                return outcome

            patch_attempts += 1
            outcome.patch_attempts = patch_attempts
            plan.patch_id = (f"{group.criterion.replace('.', '-')}-"
                             f"{group.component.strip('#').replace('>', '-').replace(' ', '')}"
                             f"-{patch_attempts}")

            # One copy of the patch, on disk. Approval and application both
            # read this file; nothing regenerates it.
            path, fingerprint = write_plan(self.root, self.audit.run_id, plan)
            outcome.patch_files.append(str(path))

            if self.approve is not None and not self.approve(path, plan):
                outcome.status = "rejected"
                outcome.reason = "a person declined this patch"
                return outcome

            approved = read_plan(path, expect_fingerprint=fingerprint)
            applied = apply_plan(approved, self.workdir)
            if not applied.applied:
                # An apply failure is a locate failure in disguise: the text
                # did not match. Feed the real lines back and try again.
                retry_note = (f"\n\nYour previous edit did not apply: {applied.reason}. "
                              f"Here is what is really in the file:\n"
                              + locate_context(target.read_text(encoding="utf-8"),
                                               approved.edits[0].find if approved.edits else ""))
                patch_attempts -= 1
                outcome.locate_attempts += 1
                # The text stopped matching between planning and applying:
                # a locate problem, so it does not spend a patch attempt.
                if outcome.locate_attempts >= MAX_LOCATE_ATTEMPTS * MAX_PATCH_ATTEMPTS:
                    if outcome.closed:
                        outcome.status = "closed"
                        outcome.reason = ("closed by an earlier patch in this "
                                          "group; nothing remained to locate")
                        return outcome
                    outcome.status = "could_not_locate"
                    outcome.reason = applied.reason
                    return outcome
                continue

            closed, created = self.reaudit(group)
            if self.blocked:
                # The patch applied and then did not build. That is a different
                # failure from "the fix did not work", and the compiler already
                # said what is wrong -- so say it, and start the next attempt
                # from the file as it was rather than from the wreck.
                target.write_text(original, encoding="utf-8")
                outcome.status = "still_failing"
                outcome.reason = self.blocked
                retry_note = ("\n\nYour previous patch was applied and the project "
                              "then FAILED TO BUILD. The edit was not valid code. "
                              "Here is what the build said:\n"
                              + self.blocked[-700:]
                              + "\n\nStart again from the original file, keep the "
                                "JSX balanced, and never move a closing tag away "
                                "from its opening tag.")
                self.blocked = ""
                continue
            # Only this group's own targets count as closed by this patch.
            mine = {f"{group.criterion}:{t}" for t in group.targets}
            outcome.closed = sorted(set(closed) & mine)
            outcome.created = created

            # All of them, not any of them. Stopping on the first target to
            # close reported a group as done while two of its three planted
            # instances were still open, which is the "stops at the first
            # occurrence" failure the three-instance benchmark exists to catch.
            remaining = sorted(mine - set(outcome.closed))
            if self.lessons is not None:
                # Written whether or not it worked. A fix that failed is the
                # only thing that stops the next attempt repeating it.
                self.lessons.record(Lesson(
                    criterion=group.criterion,
                    element_shape=group_shape(group),
                    component=group.component,
                    fix_applied=describe_fix(approved),
                    closed=not remaining and not created,
                    reaudit_said=("" if not remaining else
                                  f"{len(remaining)} of {len(mine)} still failing: "
                                  + ", ".join(r.split(":", 1)[1] for r in remaining[:3])),
                    find_text=approved.edits[0].find if approved.edits else "",
                    replace_text=approved.edits[0].replace if approved.edits else "",
                    patch_attempt=patch_attempts))
            if not remaining and not created:
                outcome.status = "closed"
                return outcome
            if remaining:
                retry_note = ("\n\nThat patch fixed "
                              f"{len(outcome.closed)} of {len(mine)} instances. "
                              "These are still failing and must be fixed too, using "
                              "the same technique: " + ", ".join(
                                  r.split(":", 1)[1] for r in remaining[:4]) + ".")
                continue

            # Every target closed, and new failures in their place. Net zero,
            # so the file goes back -- and with it the claim: a patch that was
            # undone has closed nothing, whatever the re-audit saw while it was
            # applied. Leaving outcome.closed at 2 sent a run to the pull
            # request step with an empty diff.
            target.write_text(original, encoding="utf-8")
            outcome.closed, outcome.created = [], []
            retry_note = ("\n\nYour previous patch applied cleanly but the re-audit "
                          "still reports the problem. The fix was wrong, not the "
                          "location. Try a different technique.")

        outcome.status = "still_failing"
        outcome.reason = outcome.reason or (
            f"still failing after {MAX_PATCH_ATTEMPTS} patches")
        if not outcome.closed:
            # Leave the tree as we found it. A change nobody could verify must
            # not travel on into a diff or a pull request.
            target.write_text(original, encoding="utf-8")
        return outcome

    # -- the locate retry loop -------------------------------------------

    def _locate(self, group: Group, source_path: str, findings_text: str,
                lesson_text: str, retry_note: str):
        """Edits whose `find` matches exactly once. Own budget of three.

        Returns (plan or None, attempts used, last failure reason). The budget
        is per call, not per group, so a patch retry never spends it.
        """
        target = self.workdir / source_path
        source = target.read_text(encoding="utf-8")
        used = 0
        last = ""

        while used < MAX_LOCATE_ATTEMPTS:
            used += 1
            try:
                data = request_edits(self.client, group.criterion, group.component,
                                     findings_text, source_path, source,
                                     lesson_text, retry_note)
            except Exception as exc:
                last = f"{type(exc).__name__}: {str(exc)[:120]}"
                retry_note = ("\n\nThe previous response could not be read: " + last)
                continue

            edits = [Edit(**e) for e in data.get("edits", [])]
            if not edits:
                last = "the response contained no edits"
                retry_note = "\n\nYour previous response contained no edits."
                continue

            # Resolve each `find` against the file before judging it. A model
            # that got the tokens right and the indentation wrong has still
            # named the right place, and rewriting `find` to the real bytes
            # keeps the patch a literal replacement.
            resolved, bad = [], []
            for e in edits:
                real = resolve_find(source, e.find)
                if real is None:
                    bad.append(e)
                else:
                    resolved.append(dataclasses.replace(e, find=real))
            if not bad:
                edits = resolved
            if bad:
                e = bad[0]
                n = source.count(e.find)
                last = (f"the find text matched {n} times"
                        + (" (already fixed, or never there)" if n == 0 else ""))
                retry_note = (
                    f"\n\nYour previous `find` matched {n} times, and it must match "
                    "exactly once. " + ("It is not in the file at all; here is what "
                                        "is really there:\n" if n == 0 else
                                        "Include more surrounding lines to make it "
                                        "unique. Nearby text:\n")
                    + locate_context(source, e.find))
                continue

            return (PatchPlan(patch_id="pending", criterion=group.criterion,
                              component=group.component,
                              rationale=data.get("rationale", ""), edits=edits,
                              addresses=[f.summary for f in group.findings],
                              locate_attempts=used),
                    used, "")

        return None, used, last

    def _serve(self) -> bool:
        """Put the patched build on port 3000. True once it answers.

        Two shapes. A folder is served by SPA_SERVER, which falls back to
        index.html so a client-side route resolves instead of 404ing -- without
        that, a re-audit of /audit-trail gets a 404 page, which fails no check
        and reads as a clean fix. A Next.js project has no such folder, so its
        own server is started instead.
        """
        session = self.audit.session
        session.exec("pkill -f 'http.server 3000'; pkill -f ally_serve.py; "
                     "pkill -f 'next start'; pkill -f 'npm start'; sleep 1; true",
                     timeout=120)

        if self.serve_root.startswith("server:"):
            root = self.serve_root[len("server:"):]
            session.exec(
                f"cd {root} && PORT=3000 NODE_OPTIONS=--max-old-space-size=512 "
                "nohup npm start > /tmp/patched.log 2>&1 & echo ok", timeout=90)
            wait = 60
        else:
            session.sb.fs.upload_file(SPA_SERVER.encode(), "/tmp/ally_serve.py")
            session.exec(f"nohup python3 /tmp/ally_serve.py {self.serve_root} "
                         "> /tmp/patched.log 2>&1 & echo ok", timeout=90)
            wait = 20

        probe = ("python3 -c \"import urllib.request as u; "
                 "print(u.urlopen('http://127.0.0.1:3000/', timeout=4).status)\" "
                 "2>/dev/null || echo down")
        deadline = time.time() + wait
        while time.time() < deadline:
            if "200" in (session.exec(probe, timeout=30).result or ""):
                return True
            time.sleep(2)
        return False

    # -- rebuild and re-audit --------------------------------------------

    def reaudit(self, group: Group) -> tuple[list[str], list[str]]:
        """Serve the patched tree, run the same checks on the same states.

        Returns (closed, created) as criterion:state keys. A finding is closed
        only because the check no longer reports it, never because a patch
        applied.
        """
        # Closure is measured per TARGET, not per criterion:state. There are
        # three planted instances of each criterion, so a group that fixes one
        # of them would never clear a criterion-level key and no patch could
        # ever be recorded as closing anything.
        # Scoped to this group's page. `after` comes from re-auditing one
        # page, so comparing it against failures found on every page would
        # report each of those as closed without re-testing any of them.
        page = group.page or self.audit.url
        before = {f"{r.criterion}:{t}" for r in self.audit.results
                  if r.status == "failed" and (r.page or self.audit.url) == page
                  for t in (r.targets or ("page",))}

        # Rebuild first. Editing app/page.tsx changes nothing a browser can
        # see until the bundler has run over it, and re-auditing the old build
        # would measure the patch as having done nothing.
        if self.build:
            # The browser is back: the previous re-audit started it, and it is
            # the largest process in a 1 GiB box. Stopping it here is why the
            # first build succeeded and the second was killed. record() starts
            # it again immediately below.
            self.audit.session.exec("pkill -f chromium; sleep 2; true", timeout=120)
            self.audit.session._ready = False

            # Piping into `tail` hands back tail's exit code, which is always
            # zero. A build that was killed for running out of memory reported
            # success, the half-written output was served, and the re-audit
            # scored a page that had never rendered.
            out = self.audit.session.exec(
                f"{self.build} > /tmp/build.log 2>&1; echo EXIT:$?; tail -5 /tmp/build.log",
                timeout=1200)
            text = (out.result or "").strip()
            if "EXIT:0" not in text:
                why = "ran out of memory" if "EXIT:137" in text else "failed"
                self.blocked = (f"the patched tree {why} while building, so the "
                                "patch could not be re-audited: " + text[-200:])
                return [], []

        if not self._serve():
            self.blocked = ("the patched tree built but nothing answered on "
                            "port 3000, so the patch could not be re-audited")
            return [], []

        after: set[str] = set()
        not_eval: list[str] = []
        from urllib.parse import urlparse
        local = "http://localhost:3000" + (urlparse(page).path or "/")

        from . import checks as checks_mod
        for state in self.audit.states:
            rec = self.audit.session.record(local, state,
                                            run_id=f"{self.audit.run_id}-reaudit")
            # A patched page that did not render is not a clean page. Every
            # check would return not_evaluated, `after` would come back empty,
            # and `before - after` would report every finding as closed without
            # one of them having been re-tested. Refusing to score is the only
            # honest answer here.
            if rec.loaded_note:
                self.blocked = ("the patched page did not render, so nothing "
                                "could be re-tested: " + rec.loaded_note)
                return [], []
            for criterion, fn in checks_mod.CHECKS.items():
                with checks_mod.criterion_tag(criterion, state, local):
                    r = fn(rec) if criterion != "2.4.3" else fn(rec, judge=self.audit.judge)
                if r.status == "failed":
                    for t in (r.targets or ("page",)):
                        after.add(f"{criterion}:{t}")
                elif r.status == "not_evaluated":
                    not_eval.append(f"{criterion}:{state}")

        self.last_not_evaluated = not_eval
        return sorted(before - after), sorted(after - before)

    # -- the whole pass ---------------------------------------------------

    def run(self, source_path: str, sources: dict | None = None) -> list[FixOutcome]:
        """`sources` maps a page URL to the file that draws it.

        Without it every group was sent the entry page's source file. On a
        React app that file is index.html -- seven lines, no control in it --
        so a finding on /verify was handed a file its fix could not be in.
        """
        groups = group_findings(self.audit.results)
        print(f"\n{len(groups)} group(s) to fix "
              f"(findings grouped by component, then criterion)")
        for g in groups:
            src = (sources or {}).get(g.page) or source_path
            print(f"  [{g.criterion} {g.component}] {len(g.findings)} finding(s)"
                  f"  in {src}")
            outcome = self.fix_group(g, src)
            self.outcomes.append(outcome)
            print(f"      -> {outcome.status}  "
                  f"locate={outcome.locate_attempts} patch={outcome.patch_attempts}  "
                  f"closed={len(outcome.closed)} created={len(outcome.created)}"
                  + (f"  {outcome.reason}" if outcome.reason else ""))
        return self.outcomes

    # -- reporting --------------------------------------------------------

    def summary(self) -> dict:
        closed = sum(len(o.closed) for o in self.outcomes)
        created = sum(len(o.created) for o in self.outcomes)
        return {
            "groups": len(self.outcomes),
            "closed": closed,
            "created": created,
            "net": closed - created,
            # The two counters, never merged.
            "could_not_locate": sum(1 for o in self.outcomes
                                    if o.status == "could_not_locate"),
            "still_failing": sum(1 for o in self.outcomes
                                 if o.status == "still_failing"),
            "patch_attempts_per_closed": (
                round(sum(o.patch_attempts for o in self.outcomes if o.status == "closed")
                      / max(1, sum(1 for o in self.outcomes if o.status == "closed")), 2)
                if any(o.status == "closed" for o in self.outcomes) else None),
        }
