"""The job the product actually runs: a live URL and a repo in, a pull request out.

This is what the dashboard was missing. It used to render
`python -m agent.run <url> --source <path>` on screen and tell the reader the
dashboard does not start runs, which is not a product, it is a note about one.

One job, five phases, each of them a Weave op so the whole thing is one trace
tree a reader can open:

    clone -> audit -> fix -> re-audit -> pull request

**The loop is live inside it.** `FixLoop` is constructed with a `Lessons` table,
so every patch outcome is written down and the matching rows for that criterion
go into the next patch prompt, failures included and labelled as failures. The
retrieved row ids are attached to the patch call's trace attributes, so the
mechanism can be opened rather than taken on trust.

**Nobody types a file path.** The source file is derived by matching the live
URL against the files in the clone, because asking a person which file backs a
URL they just pasted is asking them to do the agent's job.
"""

from __future__ import annotations

import io
import os
import re
import sys
import json
import time
import uuid
import pathlib
import threading
import traceback
from dataclasses import dataclass, field, asdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))




def _op(fn):
    return fn


CHECKOUT = "/tmp/accel-job"
PHASES = ["clone", "audit", "fix", "reaudit", "pr"]


# --------------------------------------------------------------------------
# Job state, which the UI polls
# --------------------------------------------------------------------------

@dataclass
class Event:
    at: float
    phase: str
    message: str
    level: str = "info"


@dataclass
class Job:
    id: str
    url: str
    repo: str
    #: Which branch to read the source from. Empty means the repository's
    #: default. It exists so a fix can be prepared against a branch that is not
    #: merged yet -- auditing the deployed page while patching the branch that
    #: will replace it.
    branch: str = ""
    #: "queued" | "running" | "done" | "failed"
    status: str = "queued"
    phase: str = "clone"
    source: str = ""
    events: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    recordings: dict = field(default_factory=dict)
    axe: dict = field(default_factory=dict)
    patches: list = field(default_factory=list)
    lesson_ids: list = field(default_factory=list)
    #: The sandbox's noVNC stream. Watching the browser being driven is the
    #: whole point of running it in a sandbox rather than headless on a laptop.
    watch_url: str = ""
    #: Every page this run visited, in the order it reached them.
    pages: list = field(default_factory=list)
    #: One entry per parallel sandbox, each with its own live desktop.
    lanes: list = field(default_factory=list)
    pr_url: str = ""
    pr_blocked: str = ""
    diff: str = ""
    trace_url: str = ""
    error: str = ""
    #: Who started this. Empty for a run started from the command line.
    user_id: str = ""
    #: Their GitHub token, used to clone and to open the pull request. Stripped
    #: in to_dict: this object is serialised straight to the browser, and
    #: `asdict` takes every field, so a secret added here without a matching
    #: line below is a secret published to whoever opens the page.
    github_token: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def say(self, phase: str, message: str, level: str = "info") -> None:
        self.phase = phase
        self.events.append(Event(time.time(), phase, message, level))

    #: Never sent to the browser. See Job.github_token.
    SECRET_FIELDS = ("github_token",)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in self.SECRET_FIELDS:
            d.pop(k, None)
        d["events"] = [asdict(e) for e in self.events]
        d["elapsed"] = round((self.finished or time.time()) - self.started, 1)
        return d


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def get(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def recent(limit: int = 20) -> list[dict]:
    with _LOCK:
        js = sorted(JOBS.values(), key=lambda j: j.started, reverse=True)[:limit]
    return [{"id": j.id, "url": j.url, "repo": j.repo, "status": j.status,
             "phase": j.phase, "findings": len(j.findings), "pr_url": j.pr_url,
             "pages": len(j.pages), "watch_url": j.watch_url,
             "started": j.started} for j in js]


# --------------------------------------------------------------------------
# Understanding what the user pasted
# --------------------------------------------------------------------------

GITHUB = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)"
    r"(?:\.git)?/?$")


def parse_repo(text: str) -> tuple[str, str] | None:
    """"https://github.com/owner/name" -> ("owner", "name")."""
    m = GITHUB.match((text or "").strip())
    return (m.group(1), m.group(2)) if m else None


def guess_source(session, url: str, checkout: str) -> str:
    """Which file in the clone backs this URL?

    Asking the person who pasted the URL to also name the file is asking them to
    do the agent's job. The last path segment of the URL is the strongest signal,
    then an index file in a directory that looks like a web root.
    """
    listing = session.exec(
        f"cd {checkout} && git ls-files | grep -Ei "
        f"'\\.(html|htm|jsx|tsx|vue|svelte|astro)$' | head -600", timeout=120)
    files = [f.strip() for f in (listing.result or "").splitlines() if f.strip()]
    if not files:
        return ""

    # Framework routes first. Next.js App Router puts the page for "/" in
    # app/page.tsx and for "/pricing" in app/pricing/page.tsx; the Pages Router
    # uses pages/index.tsx. A run against clearway picked app/layout.tsx, because
    # the old fallback was "shallowest file wins" and layout sorts first.
    from urllib.parse import urlparse

    route = (urlparse(url).path or "/").strip("/")
    candidates = []
    if route:
        candidates += [f"app/{route}/page.tsx", f"app/{route}/page.jsx",
                       f"app/{route}/page.js", f"pages/{route}.tsx",
                       f"pages/{route}.jsx", f"src/app/{route}/page.tsx",
                       f"src/pages/{route}.tsx"]
    else:
        candidates += ["app/page.tsx", "app/page.jsx", "app/page.js",
                       "pages/index.tsx", "pages/index.jsx",
                       "src/app/page.tsx", "src/pages/index.tsx"]
    lower = {f.lower(): f for f in files}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]

    tail = url.rstrip("/").rsplit("/", 1)[-1] or "index.html"
    stem = tail.split("?")[0].split("#")[0]
    if stem and not stem.endswith((".html", ".htm")):
        stem = stem + ".html"

    exact = [f for f in files if f.rsplit("/", 1)[-1].lower() == stem.lower()]
    if exact:
        # Shallowest wins: public/2-1-1.html beats deep/nested/copy/2-1-1.html.
        return sorted(exact, key=lambda f: f.count("/"))[0]

    roots = ("public/", "static/", "dist/", "site/", "www/", "src/")
    indexes = [f for f in files if f.rsplit("/", 1)[-1].lower() in ("index.html", "index.htm")]
    preferred = [f for f in indexes if f.startswith(roots)] or indexes
    if preferred:
        return sorted(preferred, key=lambda f: f.count("/"))[0]
    return sorted(files, key=lambda f: f.count("/"))[0]


MAX_PAGES = 6
MAX_LANES = 4


def find_pages(session, url: str, limit: int = MAX_PAGES) -> list:
    """Same-origin pages linked from the first one, the first `limit` of them.

    A multipage app audited at its front door tells you about the front door.
    Clearway is one, and the first run against it looked at a single URL.

    Deliberately shallow: the links on the entry page, in document order, not a
    full crawl. It is the difference between auditing a site and auditing a
    landing page, without turning the product into a spider.
    """
    js = """
    (function () {
      var here = location.origin, seen = {}, out = [];
      var links = document.querySelectorAll('a[href]');
      for (var i = 0; i < links.length; i++) {
        var u;
        try { u = new URL(links[i].getAttribute('href'), location.href); }
        catch (e) { continue; }
        if (u.origin !== here) continue;
        if (/\\.(pdf|zip|png|jpe?g|svg|gif|mp4|webm|css|js)$/i.test(u.pathname)) continue;
        u.hash = '';
        var s = u.toString().replace(/\\/$/, '');
        if (s === location.href.replace(/\\/$/, '').split('#')[0]) continue;
        if (seen[s]) continue;
        seen[s] = 1;
        out.push(s);
      }
      return out;
    })()
    """
    body = [
        "import json, urllib.request, time",
        "from websocket import create_connection",
        "tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9222/json'))",
        "page = next(t for t in tabs if t['type'] == 'page')",
        "ws = create_connection(page['webSocketDebuggerUrl'], timeout=60)",
        "mid = 0",
        "def send(m, p=None):",
        "    global mid",
        "    mid += 1",
        "    ws.send(json.dumps({'id': mid, 'method': m, 'params': p or {}}))",
        "    while True:",
        "        r = json.loads(ws.recv())",
        "        if r.get('id') == mid: return r",
        "send('Page.enable')",
        f"send('Page.navigate', {{'url': {url!r}}})",
        "time.sleep(3.5)",
        "r = send('Runtime.evaluate', {'expression': " + repr(js) + ", 'returnByValue': True})",
        "print('LINKS ' + json.dumps(r.get('result', {}).get('result', {}).get('value') or []))",
        "ws.close()",
    ]
    script = "/tmp/ally_links.py"
    session.sb.fs.upload_file(chr(10).join(body).encode(), script)
    out = session.exec(f"cd /tmp && python3 ally_links.py 2>&1 | tail -3", timeout=240)
    line = next((l for l in (out.result or "").splitlines() if l.startswith("LINKS")), None)
    if not line:
        return []
    try:
        return json.loads(line[len("LINKS "):])[: limit - 1]
    except Exception:
        return []


# --------------------------------------------------------------------------
# The phases
# --------------------------------------------------------------------------

class BuildFailed(RuntimeError):
    """The repository could not be built here, with what the build said."""


#: The sandbox has 1 GiB and reports the host's 64 cores, so a bundler that
#: sizes its worker pool from `nproc` starts 64 workers inside a gibibyte and
#: the kernel kills it. Measured on BitEstate: `npm run build` was Killed with
#: exit 137, and the same build with one worker finished in 9 seconds.
BUILD_ENV = ("PARCEL_WORKERS=1 JOBS=1 UV_THREADPOOL_SIZE=2 "
             "RAYON_NUM_THREADS=1 NEXT_TELEMETRY_DISABLED=1 CI=1 "
             "NODE_OPTIONS=--max-old-space-size=768")

#: Installing is not bundling. npm needs very little heap, and a large
#: --max-old-space-size makes V8 lazy about collecting: it grows towards a
#: limit the cgroup will not allow and the kernel kills it first. Clearway's
#: 590 packages installed in 26 seconds in an empty sandbox and were killed in
#: a reused one, with the bundler's setting inherited.
INSTALL_ENV = ("NPM_CONFIG_AUDIT=false NPM_CONFIG_FUND=false CI=1 "
               "NPM_CONFIG_MAXSOCKETS=2 NPM_CONFIG_PROGRESS=false "
               "NODE_OPTIONS=--max-old-space-size=384")


def free_memory_for_build(session) -> str:
    """Stop the browser so the bundler can have the memory.

    The sandbox has 1 GiB and the audit session is holding a Chromium with a
    software rasteriser, plus Xvfb, xfce4, x11vnc and noVNC. A build that
    finishes in nine seconds in an empty sandbox is killed in this one. The
    re-audit restarts the browser itself, so stopping it here costs nothing.
    """
    # `free` reports the HOST's memory inside a Daytona sandbox, the same way
    # nproc reports the host's cores. The cgroup is the only honest number.
    probe = ("awk '{printf \"%d\", $1/1048576}' /sys/fs/cgroup/memory.current "
             "2>/dev/null; echo -n ' of '; "
             "awk '{printf \"%d\", $1/1048576}' /sys/fs/cgroup/memory.max 2>/dev/null")
    before = session.exec(probe, timeout=60)
    # Everything a previous run may have left behind, not just the browser: a
    # node server from the last re-audit, a static server, a stray chromium.
    # `next build` succeeds in a fresh sandbox with the same settings that it
    # is killed with here, and the difference is what else is still resident.
    session.exec("pkill -f chromium; pkill -f ally_serve.py; "
                 "pkill -f 'http.server 3000'; pkill -f 'next start'; "
                 "pkill -f 'npm start'; pkill -f node; sleep 2; true", timeout=120)
    session._ready = False          # so the next record() starts it again

    # The desktop as well. Killing only the browser left 370MB of 1024 in use
    # and the build was still killed; Xvfb, xfce4, x11vnc and noVNC are the
    # rest of it. Both come back for the re-audit, which starts them itself.
    try:
        session.sb.computer_use.stop()
        session._desktop = False
    except Exception:
        pass
    # Page cache counts against the cgroup, and a reused sandbox carries a lot
    # of it after a clone and an install.
    session.exec("sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null; true",
                 timeout=60)
    after = session.exec(probe, timeout=60)
    return (f"memory in use {(before.result or '?').strip()}MB, "
            f"{(after.result or '?').strip()}MB with the browser and desktop "
            f"stopped for the build")


def prepare_build(session, checkout: str) -> tuple[str, str]:
    """Install and build the checkout once. Returns (what to serve, how to rebuild).

    The re-audit serves the patched tree over a static file server, which is
    right for a plain HTML page and wrong for every framework: a Vite repo's
    index.html asks for /src/main.tsx, a static server hands that back as
    TypeScript, and the page renders nothing.

    Returns ("", "") when the repository produces nothing servable, so the
    caller can say the fix step cannot run rather than re-auditing a blank page.
    """
    has = session.exec(f"cd {checkout} && test -f package.json && echo yes || echo no",
                       timeout=60)
    if "yes" not in (has.result or ""):
        # No package.json means no build. For a hand-written HTML page that is
        # correct and the tree is served as it stands. For a bundled app whose
        # package.json is missing from the repository -- BitEstate has a
        # package-lock.json and no package.json -- the served index.html asks
        # for /src, nothing renders, and the re-audit refuses to score. Saying
        # so here beats discovering it three minutes later.
        bundled = session.exec(
            f"cd {checkout} && grep -lE 'src=\"/?src/|type=\"module\"' "
            f"index.html 2>/dev/null | head -1", timeout=60)
        if (bundled.result or "").strip():
            return "", ""
        return checkout, ""            # a static site: serve it as it stands

    got = session.exec(
        f"cd {checkout} && ({INSTALL_ENV} npm ci --no-audit --no-fund || "
        f"{INSTALL_ENV} npm install --no-audit --no-fund) "
        f"> /tmp/npm-install.log 2>&1; echo EXIT:$?; tail -4 /tmp/npm-install.log",
        timeout=1500)
    # The install's exit code was never read, so a failed install went quietly
    # on to a build that reported "next: not found" -- which reads as a missing
    # dependency rather than as the install that never finished.
    itext = got.result or ""
    if "EXIT:0" not in itext:
        why = "ran out of memory" if "EXIT:137" in itext else "failed"
        raise BuildFailed(f"installing the dependencies {why}: "
                          + itext.replace("EXIT:", "exit ")[-300:])
    build = f"cd {checkout} && {BUILD_ENV} npm run build"
    out = session.exec(f"{build} > /tmp/build.log 2>&1; echo EXIT:$?; tail -4 /tmp/build.log",
                       timeout=1500)
    text = out.result or ""
    if "EXIT:0" not in text:
        why = "ran out of memory" if "EXIT:137" in text else "failed"
        raise BuildFailed(f"the build {why}: " + text.replace("EXIT:", "exit ")[-300:])

    found = session.exec(
        f"cd {checkout} && for d in dist out build public; do "
        f'test -f "$d/index.html" && echo "$d" && break; done', timeout=60)
    lines = [l.strip() for l in (found.result or "").splitlines() if l.strip()]
    if lines:
        return f"{checkout}/{lines[-1]}", build

    # Built successfully, and produced no folder anyone can serve. Next.js is
    # the case that matters: `.next` is not a site, the app is a server. If the
    # project declares a start script, the loop runs it instead of serving a
    # directory.
    has_start = session.exec(
        f"cd {checkout} && node -e "
        f'"process.exit(require(\'./package.json\').scripts?.start ? 0 : 1)"'
        f" && echo yes || echo no", timeout=120)
    if "yes" in (has_start.result or ""):
        return f"server:{checkout}", build

    # No build output and no server: this repository is served from its source
    # tree, which is only true of a hand-written page.
    return checkout, ""


def clone_repo(session, owner: str, name: str, job_id: str,
               branch: str = "", token: str = "") -> str:
    """A shallow clone of the repo the user named, inside the sandbox.

    Authenticated with the signed-in person's token when there is one, because
    an anonymous clone of a private repository fails with a 404 that reads
    exactly like a typo in the name.
    """
    auth = f"x-access-token:{token}@" if token else ""
    url = f"https://{auth}github.com/{owner}/{name}.git"
    at = f"--branch {branch} " if branch else ""
    r = session.exec(
        f"rm -rf {CHECKOUT} && git clone --depth 1 {at}-q {url} {CHECKOUT} && "
        f"cd {CHECKOUT} && git rev-parse --short HEAD", timeout=420)
    out = (r.result or "").strip()
    if not out or "fatal" in out.lower():
        raise RuntimeError(f"could not clone {owner}/{name}: {out[:200] or 'no output'}")
    return out.splitlines()[-1].strip()


def _can_push(owner: str, name: str, token: str = "") -> bool:
    """Does the signed-in person have write access here?

    Asked with their token, not the machine's. The previous version shelled out
    to `gh`, which answers for whoever is logged in on the server -- correct on
    one laptop, meaningless once other people use this.
    """
    if not token:
        return False
    import json as _json, urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{name}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "accel"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return bool(_json.loads(r.read()).get("permissions", {}).get("push"))
    except Exception:
        return False


def accel_run(job_id: str, url: str, repo: str, branch: str,
             states: list, fix: bool, pr: bool) -> dict:
    """One run, as one trace.

    Everything the job does is already an op -- clone, discover, build, patch,
    re-audit, pull request -- but each of them was a root call, so a run was
    two hundred unrelated rows in Weave instead of one tree. The arguments are
    primitives and the return is a summary, so the trace carries what a reader
    wants without serialising the whole mutable Job on either end.
    """
    job = get(job_id)
    if job is None:
        raise RuntimeError(f"no such job {job_id}")
    _run(job, states or [], fix, pr)
    failed = [f for f in job.findings if f.get("status") == "failed"]
    return {
        "pages": len(job.pages),
        "checks": len(job.findings),
        "findings": len(failed),
        "closed": sum(p.get("closed", 0) for p in job.patches),
        "created": sum(p.get("created", 0) for p in job.patches),
        "pr_url": job.pr_url,
        "source": job.source,
    }


def _run(job: Job, states: list[str], want_fix: bool, want_pr: bool) -> None:
    from accel.agent.audit import Audit
    from accel.agent.fixloop import FixLoop
    from accel.agent.lessons import Lessons
    from accel.agent.fixloop import RemoteTree
    from accel.agent import pr as pr_mod

    parsed = parse_repo(job.repo)
    if not parsed:
        raise RuntimeError(f"{job.repo!r} is not a GitHub repository URL")
    owner, name = parsed

    job.say("clone", f"Cloning {owner}/{name}")
    audit = Audit(job.url, sandbox_id=os.environ.get("ALLY_SANDBOX"),
                  states=states or ["loaded"], run_id=job.id)
    try:
        if audit.session.start_desktop():
            job.watch_url = audit.session.watch_url()
    except Exception:
        pass
    sha = clone_repo(audit.session, owner, name, job.id, job.branch,
                     job.github_token)
    job.say("clone", f"At {sha}")

    job.source = guess_source(audit.session, job.url, CHECKOUT)
    if not job.source:
        raise RuntimeError("no HTML-like file found in the repository to patch")

    # The loaded page always. Whether the menu and dialog passes are worth
    # running is a question about the page, not a question for whoever pasted the
    # URL: the recorder reports how many elements declare themselves a menu
    # trigger or a dialog, and that decides it.
    # Ask the repository what pages exist, before auditing anything. Crawling
    # a[href] only finds what the entry page links: BitEstate's router declares
    # ten routes and its landing page links to two, the rest being behind a
    # login. The source has no such blind spot.
    #
    # This runs BEFORE the first audit on purpose. It used to audit the entry
    # page alone, then discover, then start the rest -- so for the first minute
    # there was one sandbox on screen and nothing to suggest more were coming.
    from accel.server.fleet import Lane, MAX_SANDBOXES, run_lanes
    from accel.server.routes import routes_from_repo, sources_by_page

    # Discover more routes than there are sandboxes. A route that turns out to
    # render a screen already seen frees its slot for the next one.
    all_urls = routes_from_repo(audit.session, CHECKOUT, job.url, limit=12)
    if not all_urls:
        all_urls = [job.url]
    urls = all_urls[:MAX_SANDBOXES]

    # The file behind each page. The router is the only thing that knows, and
    # it also answers for the entry page: guess_source picks index.html on a
    # React project, which is seven lines with no control in it.
    try:
        page_sources = sources_by_page(audit.session, CHECKOUT, job.url)
    except Exception:
        page_sources = {}
    job.source = page_sources.get(job.url.rstrip("/")) or job.source
    job.say("clone", f"The page is backed by {job.source}")

    if len(urls) > 1:
        job.say("audit", f"{len(urls)} routes declared in the repository: "
                         + ", ".join(u.replace(job.url.rstrip('/'), '') or '/'
                                     for u in urls))
    else:
        # One declared route. Try the links on the page next -- a static site
        # with no framework has neither route files nor a router config -- and
        # only then treat the page as a flow whose steps are its pages.
        # Both of these drive a browser over CDP, and neither starts one. It
        # worked only because a warm sandbox happened to have Chromium running
        # from an earlier run -- and once the fix step started cleaning up
        # after itself, the next run's discovery found nothing and reported a
        # four-step page as a one-page site.
        audit.session.start_browser()
        linked = find_pages(audit.session, job.url, limit=MAX_SANDBOXES)
        if linked:
            job.say("audit", f"one declared route, {len(linked)} linked page(s)")
            urls = [job.url] + linked[: MAX_SANDBOXES - 1]
        else:
            from accel.server.steps import discover_steps, register_step_state

            steps = discover_steps(audit.session, job.url, limit=MAX_SANDBOXES - 1)
            if steps:
                job.say("audit", f"one route and no links; {len(steps)} step(s) "
                                 "inside this page: "
                                 + ", ".join(x["label"] for x in steps))

    lanes = [Lane(index=0, url=urls[0])]
    if len(urls) > 1:
        lanes += [Lane(index=i + 1, url=u) for i, u in enumerate(urls[1:])]
    elif "steps" in dir() and steps:
        for i, st in enumerate(steps):
            name = register_step_state(f"step-{i + 1}", st["selector"])
            lanes.append(Lane(index=i + 1, url=job.url, state=name,
                              label=st["label"]))

    job.lanes = [l.to_dict() for l in lanes]
    job.say("audit", f"{len(lanes)} sandbox(es) starting together")

    def say(i: int, msg: str, level: str) -> None:
        job.say("audit", f"[{i}] {msg}", level)
        job.lanes = [l.to_dict() for l in lanes]

    run_lanes(lanes, states or ["loaded"], audit.judge, say,
              tag={"job": job.id, "repo": job.repo})
    job.lanes = [l.to_dict() for l in lanes]

    # Different URL, same screen. BitEstate serves Home at both "/" and "/home",
    # so four sandboxes can spend their time auditing two pages twice. A lane
    # whose render matches one already seen is marked as a duplicate, its
    # findings are dropped rather than double counted, and its slot is spent on
    # a route nobody has looked at yet.
    queue = [u for u in all_urls[MAX_SANDBOXES:]]
    seen: dict = {}
    for wave in range(2):
        fresh = []
        for lane in lanes:
            if lane.status != "done" or not lane.signature:
                continue
            first = seen.get(lane.signature)
            if first is None:
                seen[lane.signature] = lane.url
            elif lane.url != first:
                lane.duplicate_of = first
                fresh.append(lane)
        if not fresh or not queue:
            break
        replacements = []
        for lane in fresh:
            if not queue:
                break
            nxt = queue.pop(0)
            job.say("audit", f"[{lane.index}] {lane.url} renders the same screen as "
                             f"{lane.duplicate_of}; trying {nxt} instead", "warn")
            replacements.append(Lane(index=lane.index, url=nxt))
        if not replacements:
            break
        run_lanes(replacements, states or ["loaded"], audit.judge, say,
                  tag={"job": job.id, "repo": job.repo})
        for r in replacements:
            for i, lane in enumerate(lanes):
                if lane.index == r.index:
                    lanes[i] = r
        job.lanes = [l.to_dict() for l in lanes]

    from accel.agent.recording import Census, Result
    from accel.agent.axe import AxeResult

    results = []
    #: Lane recordings arrive already serialised -- they cross a thread
    #: boundary as dicts -- so they are merged as dicts and never converted
    #: again. Putting them back into audit.recordings, which is typed for
    #: Recording objects, is what crashed the run at the end of the audit.
    merged: dict = {}
    for lane in lanes:
        if lane.duplicate_of:
            job.pages.append({"url": lane.url, "source": "", "findings": 0,
                              "note": f"same screen as {lane.duplicate_of}"})
            continue
        if lane.status != "done":
            job.pages.append({"url": lane.url, "source": "",
                              "findings": 0, "note": lane.note or lane.status})
            continue
        for f in lane.findings:
            fields = {k: (tuple(v) if isinstance(v, list) else v)
                      for k, v in f.items() if k != "census"}
            fields["page"] = lane.url
            r = Result(census=Census(**(f.get("census") or {})), **fields)
            results.append(r)
        short = lane.label or (lane.url.rstrip("/").rsplit("/", 1)[-1] or "home")
        for st, r in lane.recordings.items():
            merged[f"{st} · {short}"] = r
        if not audit.axe and lane.axe.get("ran"):
            # A real AxeResult, not a class built on the fly. The shim carried
            # three attributes and the pull request body asks for a fourth,
            # `overlapping()`, which ended a finished run with an
            # AttributeError at the very last step.
            audit.axe = AxeResult(ran=True, version=lane.axe.get("version", ""),
                                  violations=lane.axe.get("violations") or [])
        job.pages.append({
            "url": (f"{lane.url}  ({lane.label})" if lane.label else lane.url),
            "source": "",
            "findings": sum(1 for f in lane.findings if f.get("status") == "failed"),
            "note": ""})

    if all(l.status != "done" for l in lanes):
        raise RuntimeError(
            "no page could be audited. "
            + "; ".join(f"{l.url}: {l.note}" for l in lanes if l.note)[:300])

    failed = [r for r in results if r.status == "failed"]
    job.say("audit", f"{len(failed)} finding(s) across {len(results)} check(s) "
                     f"on {sum(1 for l in lanes if l.status == 'done')} page(s)")
    # Name the file behind every page, not just the entry one, and do it here
    # so the answer is on screen even when the fix step is skipped.
    for pg in job.pages:
        pg["source"] = page_sources.get(pg["url"].split("  (")[0], "") or pg.get("source", "")
    if job.pages and not job.pages[0].get("source"):
        job.pages[0]["source"] = job.source

    job.findings = [r.to_dict() for r in results]
    job.recordings = merged
    # The loop reads its work from here, and it was never filled: every run
    # reached the fix phase with an empty list and nothing to group. Every
    # page's findings go in; each one carries the page it came from, and the
    # loop re-audits that page to decide whether its patch held.
    audit.results = results
    first_axe = next((l.axe for l in lanes if l.status == "done" and l.axe.get("ran")),
                     None)
    job.axe = first_axe or {"ran": False, "version": "", "violations": []}

    if not (want_fix and failed):
        job.say("pr", "Nothing to fix." if not failed else "Fix step skipped.")
        return

    # The loop, live. Every outcome is written to the table and the matching
    # rows for the criterion go into the next prompt.
    job.say("fix", "Installing and building the repository so the patch can "
                   "be re-audited")
    job.say("fix", free_memory_for_build(audit.session))
    try:
        serve_root, build_cmd = prepare_build(audit.session, CHECKOUT)
    except BuildFailed as exc:
        job.say("pr", f"{exc} The findings above stand; no fix was attempted.",
                "warn")
        return
    if not serve_root:
        job.say("pr", "The repository has no package.json, so it cannot be built "
                      "from a clean clone and a patch could not be re-audited. "
                      "The findings above stand; no fix was attempted.", "warn")
        return
    job.say("fix", ("Running the app's own server, and rebuilding between patches"
                    if serve_root.startswith("server:") else
                    f"Serving {serve_root.rsplit('/', 1)[-1]}/"
                    + (" and rebuilding between patches" if build_cmd else "")))

    lessons = Lessons(job.id)
    loop = FixLoop(audit, workdir=pathlib.Path(CHECKOUT),
                   serve_root=serve_root, lessons=lessons,
                   build=build_cmd)
    loop.workdir = RemoteTree(audit.session, CHECKOUT)
    # Lets the file lookup grep the clone for the failing element's
    # class names instead of guessing from the page name.
    loop.checkout = CHECKOUT

    job.say("fix", "Writing patches, then rebuilding and re-auditing each one")
    outcomes = loop.run(job.source, page_sources)
    for o in outcomes:
        job.patches.append({
            "criterion": o.criterion, "component": o.component,
            "status": o.status, "patch_attempts": o.patch_attempts,
            "locate_attempts": o.locate_attempts,
            "closed": len(o.closed), "created": len(o.created),
            "reason": o.reason, "lesson_ids": list(o.lesson_ids)})
        job.lesson_ids.extend(o.lesson_ids)
    s = loop.summary()
    job.say("reaudit", f"{s['closed']} closed, {s['created']} created, "
                       f"{s['patch_attempts_per_closed']} attempts per fix")
    # A re-audit that could not be scored says so here rather than being read
    # as a clean sweep.
    if loop.blocked:
        job.say("reaudit", loop.blocked, "warn")

    d = audit.session.exec(f"cd {CHECKOUT} && git diff --stat && echo '---' && "
                           f"git diff", timeout=180)
    job.diff = (d.result or "")[:60000]

    if not want_pr:
        job.say("pr", "Diff ready. Pull request not requested.")
        return

    closed_total = sum(p.get("closed", 0) for p in job.patches)
    created_total = sum(p.get("created", 0) for p in job.patches)
    has_diff = bool((job.diff or "").strip().strip("-"))

    # A pull request opens whenever the source actually changed, and its title
    # says which of three outcomes it is. The previous rule was net-positive
    # or nothing, which threw away the diff, the evidence and the reasoning in
    # exactly the case where a person most wants to read them. Refusing to SEND
    # a patch as a fix and refusing to SHOW it are different refusals.
    if not has_diff:
        job.pr_blocked = ("No pull request: nothing in the source changed, so "
                          "there is no diff to open one with. The findings and "
                          "the evidence are above.")
        job.say("pr", job.pr_blocked, "warn")
        return
    if closed_total and created_total >= closed_total:
        job.say("pr", f"{closed_total} closed and {created_total} created; "
                      "opening this for review rather than as a fix", "warn")
    if not job.github_token:
        job.pr_blocked = ("No pull request: this run carries no GitHub "
                          "authorisation. The diff above is the whole change.")
        job.say("pr", job.pr_blocked, "warn")
        return
    if not _can_push(owner, name, job.github_token):
        job.pr_blocked = (f"No write access to {owner}/{name}, so no branch was "
                          f"pushed. The diff above is the complete change.")
        job.say("pr", job.pr_blocked, "warn")
        return

    job.say("pr", "Opening a pull request")
    try:
        title, body = pr_mod.build_body(audit, loop.summary(), outcomes,
                                        audit.results)
        out = pr_mod.open_pull_request(
            audit.session, owner, name, job.github_token, job.id,
            title, body, CHECKOUT, base=job.branch)
        job.pr_url = (out or {}).get("url", "")
        if job.pr_url:
            job.say("pr", job.pr_url)
        else:
            job.pr_blocked = (out or {}).get("error", "no pull request URL")
            job.say("pr", job.pr_blocked, "warn")
            if (out or {}).get("log"):
                job.say("pr", str(out["log"])[-300:], "warn")
    except Exception as exc:
        job.pr_blocked = f"{type(exc).__name__}: {str(exc)[:180]}"
        job.say("pr", job.pr_blocked, "warn")


def start(url: str, repo: str, states: list[str] | None = None,
          want_fix: bool = True, want_pr: bool = True,
          branch: str = "", user=None) -> Job:
    """Begin a job and return immediately. The UI polls it.

    `states` empty means "you decide", which is what the UI now sends.
    """
    job = Job(id=f"job-{uuid.uuid4().hex[:8]}", url=url.strip(), repo=repo.strip(),
              branch=(branch or "").strip())
    job.user_id = getattr(user, "id", "") or ""
    # The GitHub token stays on the job, never in the JSON the browser reads.
    job.github_token = getattr(user, "github_token", "") or ""
    with _LOCK:
        JOBS[job.id] = job
    try:
        from accel.server import db
        db.execute("""INSERT INTO jobs (id, user_id, url, repo, branch, status)
                      VALUES (:id, NULLIF(:uid,''), :url, :repo, :branch, 'running')
                      ON CONFLICT (id) DO NOTHING""",
                   {"id": job.id, "uid": job.user_id, "url": job.url,
                    "repo": job.repo, "branch": job.branch})
    except Exception as exc:
        job.say("start", f"could not record the job: {type(exc).__name__}", "warn")

    def body() -> None:
        job.status = "running"
        try:
            _run(job, states or [], want_fix, want_pr)
            job.status = "done"
        except Exception as exc:
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.say(job.phase, job.error, "error")
            traceback.print_exc()
        finally:
            job.finished = time.time()
            # Close the row out. Without this every run reads as still running
            # in the history, including the ones that finished hours ago.
            try:
                from accel.server import db
                db.execute("""UPDATE jobs SET status = :s, phase = :p,
                                     pr_url = NULLIF(:pr,''), finished_at = now()
                              WHERE id = :id""",
                           {"s": job.status, "p": job.phase,
                            "pr": job.pr_url or "", "id": job.id})
            except Exception:
                pass
            out = ROOT / "artifacts" / f"{job.id}.json"
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")
            except Exception:
                pass

    threading.Thread(target=body, daemon=True).start()
    return job
