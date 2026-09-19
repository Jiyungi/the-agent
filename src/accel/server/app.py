"""The dashboard: three screens, a local HTTP server, server-sent events.

    python ui/server.py [--port 8000]

Screens:
  /              Start. One URL field. The explorer finds the states.
  /runs/<id>     Run. Live view, two-column comparison, findings, patches.

Events are notifications, not data. The worker appends a line to the run's
event log; the browser is told something happened and refetches the run's JSON.
Rendered state therefore always comes from one authoritative read rather than
from deltas accumulated in the page, which is a whole class of drift bug
removed rather than debugged.

Nothing here invents a number. A run that has not produced a figure shows what
it has, and the not_evaluated count sits beside every total rather than being
left out of it.
"""

from __future__ import annotations

import io
import sys
import json
import os
import time
import argparse
import pathlib
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Run as `python ui/server.py`, sys.path[0] is ui/, not the project root, so
# `from accel.server import jobs` inside a request handler raised ModuleNotFoundError and
# the connection closed with no response at all -- a 200 on / and an empty reply
# on every API call, which reads as a server bug rather than an import one.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

STATIC = pathlib.Path(__file__).resolve().parent / "static"
ARTIFACTS = ROOT / "artifacts"
SRC = ROOT.parent
DIST = SRC / "web" / "dist"
RUNS = ROOT / "runs"


# --------------------------------------------------------------------------
# Reading what the worker wrote
# --------------------------------------------------------------------------

def list_runs() -> list[dict]:
    """Every audit on disk that has a recording in it, richest first.

    This used to glob `run-*.json` only, which hid the scored baseline runs
    behind a naming convention: those are the ones with four states and every
    screenshot present, and the ad-hoc `run-*` files are mostly one state with
    no frames left on disk. What makes a run worth opening is whether it has
    evidence, not what it happens to be called, so that is what this sorts on.
    """
    out = []
    if not ARTIFACTS.exists():
        return out
    for p in sorted(ARTIFACTS.glob("*.json")):
        if p.stem.endswith(("-score", "-stage4")) or p.stem == "trace-links":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        recs = d.get("recordings")
        if not isinstance(recs, dict) or not recs:
            continue
        s = d.get("summary", {})
        stops = [x for st in recs.values() for x in (st.get("stops") or [])]
        frames = sum(1 for x in stops
                     if x.get("screenshot") and (ROOT / x["screenshot"]).exists())
        out.append({
            "run_id": p.stem,
            "url": s.get("url", "") or next(
                (st.get("url", "") for st in recs.values() if st.get("url")), ""),
            "counts": s.get("counts", {}),
            "states": len(recs),
            "frames": frames,
            "stops": len(stops),
            "mtime": p.stat().st_mtime,
        })
    # A run with frames is worth opening; one without is a row of numbers. Among
    # those with frames, newest first -- sorting on frame count instead put an
    # older baseline generation at the top just because it had more stops.
    out.sort(key=lambda r: (r["frames"] > 0, r["mtime"]), reverse=True)
    return out


def load_run(run_id: str) -> dict | None:
    p = ARTIFACTS / f"{run_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def counts_of(run: dict) -> dict:
    c = {"passed": 0, "failed": 0, "not_evaluated": 0}
    for f in run.get("findings", []):
        c[f["status"]] = c.get(f["status"], 0) + 1
    return c


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def page(title: str, body: str, target: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — ACCEL</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<header class="bar">
  <h1><a href="/" style="color:inherit;text-decoration:none">ACCEL</a></h1>
  <nav aria-label="Screens" style="display:flex;gap:14px;font-size:13px">
    <a href="/">Start</a>
  </nav>
  <span class="target">{target}</span>
</header>
<main id="main">
{body}
</main>
</body>
</html>""".encode()


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def headline(c: dict) -> str:
    """Every total carries its not_evaluated. A run that judged nothing says so."""
    total = sum(c.values())
    judged = c.get("passed", 0) + c.get("failed", 0)
    warn = ""
    if total and judged == 0:
        warn = ('<p class="warn">Nothing was judged. Every check returned '
                'not_evaluated, so the absence of findings below means nothing '
                'could be measured, not that the page is clean.</p>')
    elif c.get("not_evaluated"):
        warn = (f'<p class="warn">{c["not_evaluated"]} of {total} checks reached no '
                f'verdict. Those criteria are not passes.</p>')
    return f"""<div class="headline">
  <span class="item"><span class="n">{c.get('failed', 0)}</span><span class="lbl">failed</span></span>
  <span class="item"><span class="n">{c.get('passed', 0)}</span><span class="lbl">passed</span></span>
  <span class="item"><span class="n">{c.get('not_evaluated', 0)}</span><span class="lbl">not evaluated</span></span>
  <span class="item"><span class="n">{total}</span><span class="lbl">checks</span></span>
  {warn}
</div>"""


def start_screen() -> bytes:
    rows = "".join(
        f'<tr><td><a href="/runs/{esc(r["run_id"])}">{esc(r["run_id"])}</a></td>'
        f'<td class="mono">{esc(r["url"])}</td>'
        f'<td>{r["states"]} state(s), {r["stops"]} stops, '
        f'{r["frames"]} frame(s)</td>'
        f'<td>{r["counts"].get("failed", 0)} failed, '
        f'{r["counts"].get("not_evaluated", 0)} not evaluated</td></tr>'
        for r in list_runs()[:25])
    body = f"""
<h2>Audit a page</h2>
<p class="sub">One URL. The explorer finds the states itself; nobody writes a
state list for a site they have never seen.</p>
<form class="card" method="get" action="/start" style="display:flex;gap:10px;flex-wrap:wrap">
  <label for="url" class="sr-only">Page address</label>
  <input id="url" name="url" type="url" required style="flex:1;min-width:280px;
         padding:9px 11px;border:1px solid var(--line-strong);border-radius:8px;
         font:inherit" placeholder="https://example.com/page"
         value="https://broken-app.vercel.app/2-1-1.html">
  <button type="submit" style="padding:9px 18px;border:1px solid var(--accent);
          background:var(--accent);color:#fff;border-radius:8px;font:inherit;
          font-weight:600;cursor:pointer">Audit</button>
</form>
<p class="sub" style="margin-top:8px">The command this runs:
<code>python -m agent.run &lt;url&gt; --source &lt;path&gt;</code></p>

<h2>Runs</h2>
<p class="sub">Newest first. Read from what the worker wrote, not from memory.</p>
{'<table class="plain"><thead><tr><th>Run</th><th>Target</th><th>Evidence</th><th>Result</th></tr></thead><tbody>'
 + rows + '</tbody></table>' if rows else
 '<div class="card">No runs yet. Nothing has been audited on this machine.</div>'}
"""
    return page("Start", body)


def live_view(run: dict) -> str:
    """Screen 2, part 1: the browser as the focus ring moves.

    One large frame, not a grid of cards: this drives one browser through one
    page, and twelve small tiles would show the same thing twelve times.

    Selecting a stop swaps the frame and moves a box onto the focused element,
    positioned from the viewport coordinates the recorder captures. Those
    coordinates are carried alongside document coordinates precisely so that
    something drawn against a viewport-sized screenshot lands in the right place
    -- the same reason the focus-delta crop uses them.
    """
    recs = run.get("recordings", {})
    states = list(recs.keys())
    if not states:
        return ('<div class="card">No recording yet. Frames appear here as the '
                'Tab run reaches each state.</div>')

    vw, vh = run.get("viewport") or (1024, 740)

    tabs = "".join(
        '<button class="tab" role="tab" id="tab-' + esc(s_) + '" '
        'aria-selected="' + ("true" if i == 0 else "false") + '" '
        'aria-controls="panel-' + esc(s_) + '" data-state="' + esc(s_) + '">'
        + esc(s_) + "</button>"
        for i, s_ in enumerate(states))

    panels = []
    for i, state in enumerate(states):
        rec = recs[state]
        stops = rec.get("stops", [])
        hidden = "" if i == 0 else " hidden"
        if not rec.get("state_reached"):
            panels.append(
                '<div role="tabpanel" id="panel-' + esc(state) + '" '
                'aria-labelledby="tab-' + esc(state) + '"' + hidden + '>'
                '<div class="card"><strong>State not reached.</strong><br>'
                '<span class="sub">' + esc(rec.get("reach_note", "")) + "</span><br>"
                "Every criterion for this state is recorded as not evaluated with "
                "that reason, rather than judged against a page we never entered."
                "</div></div>")
            continue

        items = []
        for st in stops:
            flag = ""
            delta = st.get("focus_delta")
            if st.get("obscured_by"):
                flag = ('<span class="flag">covered by '
                        + esc(st["obscured_by"])[:22] + "</span>")
            elif delta is not None and delta < 0.005:
                flag = '<span class="flag">no visible focus</span>'
            elif delta is not None:
                flag = ('<span class="delta">' + format(delta, ".2f")
                        + " changed</span>")
            name = st.get("name") or "(no accessible name)"
            items.append(
                '<li><button type="button" data-shot="'
                + esc(st.get("screenshot") or "") + '"'
                ' data-x="' + str(st.get("vx", st.get("x", 0))) + '"'
                ' data-y="' + str(st.get("vy", st.get("y", 0))) + '"'
                ' data-w="' + str(st.get("w", 0)) + '"'
                ' data-h="' + str(st.get("h", 0)) + '"'
                ' data-obscured="' + ("1" if st.get("obscured_by") else "") + '"'
                ' data-state="' + esc(state) + '"'
                ' aria-current="' + ("true" if st["index"] == 0 else "false") + '">'
                '<span class="idx">' + str(st["index"]) + "</span>"
                '<span class="nm">' + esc(name) + '<br><span class="role">'
                + esc(st.get("role") or st["tag"].lower()) + " &middot; "
                + esc(st["selector"])[:44] + "</span></span>"
                + flag + "</button></li>")

        first = next((st for st in stops if st.get("screenshot")), None)
        if first:
            frame = ('<img id="frame-' + esc(state) + '" src="/shot/'
                     + esc(first.get("screenshot") or "") + '"'
                     ' alt="The page at the selected focus stop">'
                     '<div class="box" id="box-' + esc(state) + '" hidden></div>')
        else:
            frame = ('<div class="empty">No screenshot was captured for this '
                     "state.</div>")

        panels.append(
            '<div role="tabpanel" id="panel-' + esc(state) + '" '
            'aria-labelledby="tab-' + esc(state) + '"' + hidden + ">"
            '<div class="live"><div class="frame" style="--vw:' + str(vw)
            + ";--vh:" + str(vh) + '">' + frame + "</div>"
            '<div class="stops"><ol>' + "".join(items) + "</ol></div></div>"
            '<p class="legend">Stop 0 is where focus sat when the state was '
            "entered, before any Tab. It is half the evidence for an access "
            "trap: focus was here, Tab was pressed, focus is still here. The box "
            "is the focused element&rsquo;s own rectangle, drawn from the "
            "viewport coordinates the recorder captured.</p></div>")

    script = """<script>
(function () {
  // Tabs. Arrow keys as well as clicks, because a product that reports on
  // access does not get to ship a tablist you can only use with a
  // mouse. Roving tabindex, one stop for the whole set, per the ARIA pattern.
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function select(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById('panel-' + t.dataset.state);
      if (panel) panel.hidden = !on;
    });
  }
  tabs.forEach(function (t, i) {
    t.tabIndex = i === 0 ? 0 : -1;
    t.addEventListener('click', function () { select(t); });
    t.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      var next = tabs[(tabs.indexOf(t) + d + tabs.length) % tabs.length];
      select(next);
      next.focus();
    });
  });

  // Stops. Swap the frame and move the box onto the focused element. The box is
  // positioned in percentages of the frame so it stays correct at any width.
  document.querySelectorAll('.stops button').forEach(function (b) {
    b.addEventListener('click', function () {
      var state = b.dataset.state;
      var img = document.getElementById('frame-' + state);
      var box = document.getElementById('box-' + state);
      document.querySelectorAll('.stops button[data-state="' + state + '"]')
        .forEach(function (o) { o.setAttribute('aria-current', 'false'); });
      b.setAttribute('aria-current', 'true');
      if (img && b.dataset.shot) img.src = '/shot/' + b.dataset.shot;
      if (!box) return;
      var frame = box.parentElement;
      var vw = parseFloat(frame.style.getPropertyValue('--vw')) || 1024;
      var vh = parseFloat(frame.style.getPropertyValue('--vh')) || 740;
      var w = parseFloat(b.dataset.w), h = parseFloat(b.dataset.h);
      if (!(w > 0 && h > 0)) { box.hidden = true; return; }
      box.hidden = false;
      box.style.left = (parseFloat(b.dataset.x) / vw * 100) + '%';
      box.style.top = (parseFloat(b.dataset.y) / vh * 100) + '%';
      box.style.width = (w / vw * 100) + '%';
      box.style.height = (h / vh * 100) + '%';
      box.classList.toggle('covered', b.dataset.obscured === '1');
    });
  });
})();
</script>"""

    return ('<div class="tabs-row" role="tablist" aria-label="Page states">'
            + tabs + "</div>" + "".join(panels) + script)


AXE_TAGS = {"2.1.1": "wcag211", "2.1.2": "wcag212", "2.4.3": "wcag243",
            "2.4.7": "wcag247", "2.4.11": "wcag2411"}
NAMES = {"2.1.1": "Keyboard", "2.1.2": "No Keyboard Trap", "2.4.3": "Focus Order",
         "2.4.7": "Focus Visible", "2.4.11": "Focus Not Obscured"}


def comparison(run: dict) -> str:
    """Screen 2, part 2: our five against axe, on the same page state.

    The empty cells on axe's side are the argument. They are computed from
    axe's own WCAG tags on this page, not asserted from a table.
    """
    axe = run.get("axe", {})
    if not axe.get("ran"):
        return (f'<div class="card"><strong>axe did not run.</strong> '
                f'{esc(axe.get("reason", ""))}<br><span class="sub">An empty violation '
                'list and a scan that never happened are not the same fact, so no axe '
                'column is shown for this page.</span></div>')

    by_tag: dict[str, list[str]] = {}
    for v in axe.get("violations", []):
        for t in v.get("tags", []):
            by_tag.setdefault(t, []).append(v["id"])

    findings = run.get("findings", [])
    rows = []
    #: Criteria where we reported a failure and no axe rule fired. This count is
    #: the argument the screen exists to make, so it is stated once at the top
    #: rather than left for a reader to total up from five rows.
    gaps = 0
    for crit, name in NAMES.items():
        mine = [f for f in findings if f["criterion"] == crit]
        failed = [f for f in mine if f["status"] == "failed"]
        ne = [f for f in mine if f["status"] == "not_evaluated"]
        if failed:
            ours = ('<span class="status failed">failed</span> '
                    + esc(failed[0]["summary"])[:110])
        elif ne:
            ours = ('<span class="status not_evaluated">not evaluated</span> '
                    + esc(ne[0]["reason"])[:110])
        elif mine:
            ours = '<span class="status passed">passed</span>'
        else:
            ours = '<span class="none">not run</span>'

        hits = sorted(set(by_tag.get(AXE_TAGS[crit], [])))
        theirs = (", ".join(f"<code>{esc(h)}</code>" for h in hits) if hits
                  else '<span class="none">no rule fired</span>')
        gap = '<span class="gap">only ours</span>' if failed and not hits else ""
        if gap:
            gaps += 1
        rows.append(f'<tr><td class="crit">{crit}<br><span class="role">{esc(name)}</span></td>'
                    f'<td>{ours}</td><td>{theirs}</td><td>{gap}</td></tr>')

    other = [v["id"] for v in axe.get("violations", [])
             if not any(t in AXE_TAGS.values() for t in v.get("tags", []))]
    note = (f'<p class="legend">axe also fired {len(other)} rule(s) on criteria we do '
            f'not claim: {", ".join(f"<code>{esc(o)}</code>" for o in sorted(set(other))[:8])}. '
            'Those are its findings and they go in the report unchanged.</p>'
            if other else "")

    banner = ""
    if gaps:
        banner = (f'<div class="gap-strip"><span class="gap-n">{gaps}</span>'
                  f'<span>of our five criteria found a defect on this page that '
                  f'<b>axe-core {esc(axe.get("version", ""))} did not report</b>. '
                  f'It ran, and it has no rule that fires on '
                  f'{"any of them" if gaps > 1 else "it"}.</span></div>')

    return f"""{banner}<table class="compare">
<colgroup><col><col class="ours"><col><col></colgroup>
<thead><tr><th>Criterion</th><th>ACCEL</th><th>axe-core {esc(axe.get('version', ''))}</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<p class="legend">axe's column is read from the WCAG tags on the rules it fired on
this page, not from a published table. A rule appearing there would mean we are
competing with axe on its own ground.</p>
{note}"""


def findings_table(run: dict) -> str:
    rows = []
    for f in run.get("findings", []):
        detail = f.get("summary") or f.get("reason") or ""
        ev = ", ".join(f.get("evidence_refs", [])[:3])
        rows.append(f'<tr><td class="crit">{esc(f["criterion"])}</td>'
                    f'<td>{esc(f["state"])}</td>'
                    f'<td><span class="status {f["status"]}">{f["status"].replace("_", " ")}</span></td>'
                    f'<td>{esc(detail)}</td><td class="mono">{esc(ev)}</td></tr>')
    return (f'<table class="plain"><thead><tr><th>Criterion</th><th>State</th>'
            f'<th>Status</th><th>Detail</th><th>Evidence</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def fix_section(run: dict) -> str:
    fix = run.get("fix")
    if not fix:
        return ('<div class="card">No patches in this run. It audited and stopped.</div>')
    s = fix.get("summary", {})
    rows = "".join(
        f'<tr><td class="crit">{esc(o["criterion"])}</td><td>{esc(o["component"])}</td>'
        f'<td>{esc(o["status"].replace("_", " "))}</td>'
        f'<td>{o["locate_attempts"]}</td><td>{o["patch_attempts"]}</td>'
        f'<td>{len(o["closed"])}</td><td>{len(o["created"])}</td>'
        f'<td>{esc(o.get("reason", ""))[:70]}</td></tr>'
        for o in fix.get("outcomes", []))
    return f"""<div class="headline" style="margin-bottom:12px">
  <span class="item"><span class="n">{s.get('closed', 0)}</span><span class="lbl">closed</span></span>
  <span class="item"><span class="n">{s.get('created', 0)}</span><span class="lbl">created</span></span>
  <span class="item"><span class="n">{s.get('net', 0)}</span><span class="lbl">net</span></span>
  <span class="item"><span class="n">{s.get('could_not_locate', 0)}</span><span class="lbl">could not locate</span></span>
  <span class="item"><span class="n">{s.get('still_failing', 0)}</span><span class="lbl">still failing after 5</span></span>
</div>
<table class="plain"><thead><tr><th>Criterion</th><th>Component</th><th>Outcome</th>
<th>Locate</th><th>Patch</th><th>Closed</th><th>Created</th><th>Reason</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="legend"><strong>Could not locate</strong> means the text to edit never
matched and zero patches were applied. <strong>Still failing</strong> means patches
applied cleanly and the re-audit still reports the problem. They are opposite facts
and are never added together.</p>"""


def run_screen(run_id: str) -> bytes | None:
    run = load_run(run_id)
    if run is None:
        return None
    c = counts_of(run)
    summary = run.get("summary", {})
    body = f"""
<h2>Run {esc(run_id)}</h2>
<p class="sub">{esc(summary.get('url', ''))} · states {esc(', '.join(summary.get('states', [])))}</p>
{headline(c)}

<h2>Live view</h2>
<p class="sub">The page at each focus stop. Select a stop to see the frame captured
when focus landed on it.</p>
{live_view(run)}

<h2>ACCEL against axe, same page</h2>
<p class="sub">Both halves of the report. We run axe as-is and improve none of it;
these five are what it does not cover.</p>
{comparison(run)}

<h2>Findings</h2>
<p class="sub">Every check, including the ones that reached no verdict.</p>
{findings_table(run)}

<h2>Patches</h2>
{fix_section(run)}
"""
    return page(f"Run {run_id}", body, target=summary.get("url", ""))


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a) -> None:      # quiet
        pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8", code=200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The Vite dev server runs on another port during development, so the
        # API has to say it is allowed to be called from there. In production
        # the built files are served from this same origin and this is a no-op.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200) -> None:
        self._send(json.dumps(obj).encode(), "application/json", code)

    def do_OPTIONS(self) -> None:
        self._send(b"", "text/plain", 204)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json({"error": "the request body was not JSON"}, 400)

        if u.path == "/api/session":
            # The browser posts the Supabase session here immediately after
            # sign-in. provider_token is the GitHub token and is visible
            # exactly once, in this payload -- see auth.py.
            from accel.server import auth
            user = auth.sign_in((payload.get("access_token") or "").strip(),
                                (payload.get("provider_token") or "").strip())
            if user is None:
                return self._json({"error": "that session is not valid"}, 401)
            return self._json({"login": user.github_login,
                               "avatar_url": user.avatar_url,
                               "can_touch_repos": user.can_touch_repos})

        if u.path == "/api/audit":
            from accel.server import auth, jobs

            user = auth.current(auth.bearer(self.headers))
            if user is None:
                return self._json(
                    {"error": "Sign in with GitHub to run an audit."}, 401)
            if not user.can_touch_repos:
                return self._json(
                    {"error": "Your GitHub authorisation is missing. Sign out "
                              "and sign in again to reconnect it."}, 403)

            url = (payload.get("url") or "").strip()
            repo = (payload.get("repo") or "").strip()
            if not url:
                return self._json({"error": "A page address is required."}, 400)
            if not jobs.parse_repo(repo):
                return self._json(
                    {"error": "That does not look like a GitHub repository "
                              "address. Expected github.com/owner/name."}, 400)
            states = payload.get("states") or ["loaded"]
            job = jobs.start(url, repo, states=states, user=user,
                             want_fix=bool(payload.get("fix", True)),
                             want_pr=bool(payload.get("pr", True)),
                             branch=(payload.get("branch") or "").strip())
            return self._json(job.to_dict(), 202)

        return self._json({"error": "no such endpoint"}, 404)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        p = u.path

        # The live browser view, relayed from our own origin so the sandbox's
        # preview warning never appears. A WebSocket upgrade is joined socket
        # to socket; everything else is an ordinary proxied GET.
        if p.startswith("/watch/"):
            from accel.server import watch

            sid, tail = watch.split_path(self.path)
            if not sid:
                return self._send(b"no sandbox named", "text/plain", 404)
            if "websocket" in (self.headers.get("Upgrade", "") or "").lower():
                if watch.relay(self, sid, tail):
                    self.close_connection = True
                    return
                return self._send(b"the sandbox would not upgrade",
                                  "text/plain", 502)
            code, ctype, body = watch.fetch(sid, tail or "vnc.html")
            return self._send(body, ctype, code)

        # The built React app owns the UI. Anything that is not an API call, a
        # screenshot or a stylesheet falls through to index.html so a client
        # route survives a reload.
        #
        # The server-rendered screens below are the fallback for when frontend/
        # has never been built -- they still work, so a fresh clone is not a
        # blank page, but they are not the product surface any more.
        if DIST.exists() and not p.startswith(("/api/", "/shot/", "/static/", "/events")):
            f = DIST / (p.lstrip("/") or "index.html")
            if not f.is_file():
                f = DIST / "index.html"
            if f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(f.read_bytes(), ctype)

        if p == "/":
            return self._send(start_screen())
        # ---- JSON API, which the React frontend uses --------------------
        if p == "/api/config":
            from accel.server import auth
            cfg = auth.config_for_browser()
            cfg["name"] = "ACCEL"
            return self._json(cfg)
        if p == "/api/me":
            from accel.server import auth
            user = auth.current(auth.bearer(self.headers))
            if user is None:
                return self._json({"signed_in": False})
            return self._json({"signed_in": True, "login": user.github_login,
                               "avatar_url": user.avatar_url,
                               "can_touch_repos": user.can_touch_repos})
        if p == "/api/job":
            from accel.server import jobs
            return self._json({"jobs": jobs.recent()})
        if p.startswith("/api/job/"):
            from accel.server import jobs
            job = jobs.get(p[len("/api/job/"):])
            if job is None:
                saved = load_run(p[len("/api/job/"):])
                if saved is None:
                    return self._json({"error": "no such job"}, 404)
                return self._json(saved)
            return self._json(job.to_dict())
        if p == "/api/audits":
            return self._json({"runs": list_runs()[:25]})
        if p.startswith("/api/audit/"):
            run = load_run(p[len("/api/audit/"):])
            if run is None:
                return self._json({"error": "no such audit"}, 404)
            return self._json(run)

        if p.startswith("/static/"):
            f = STATIC / p[len("/static/"):]
            if not f.exists():
                return self._send(b"not found", "text/plain", 404)
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return self._send(f.read_bytes(), ctype)
        if p.startswith("/shot/"):
            # Screenshots are served from the storage seam's refs, never
            # inlined: a data: URI would put the bytes back into the page.
            f = ROOT / p[len("/shot/"):]
            if not f.exists() or f.suffix != ".png":
                return self._send(b"not found", "text/plain", 404)
            return self._send(f.read_bytes(), "image/png")
        if p.startswith("/runs/"):
            out = run_screen(p[len("/runs/"):])
            return self._send(out or page("Not found", "<h2>No such run</h2>"),
                              code=200 if out else 404)
        if p.startswith("/api/runs/"):
            run = load_run(p[len("/api/runs/"):])
            return self._send(json.dumps(run or {}).encode(),
                              "application/json", 200 if run else 404)
        if p == "/api/runs":
            return self._send(json.dumps(list_runs()).encode(), "application/json")
        if p == "/events":
            return self._events()
        if p == "/start":
            q = parse_qs(u.query)
            url = (q.get("url") or [""])[0]
            return self._send(page("Start", f"""<h2>Run this</h2>
<p class="sub">The dashboard reads what the worker writes; it does not start one,
so there is no half-started run to explain.</p>
<div class="card"><code>python -m agent.run {esc(url)} --source &lt;path-in-repo&gt;</code></div>
<p class="sub" style="margin-top:10px"><a href="/">Back</a></p>"""))
        return self._send(b"not found", "text/plain", 404)

    def _events(self) -> None:
        """Notify, never carry data. The page refetches on every nudge."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last = None
        try:
            while True:
                latest = max((p.stat().st_mtime for p in ARTIFACTS.glob("run-*.json")),
                             default=0) if ARTIFACTS.exists() else 0
                if latest != last:
                    last = latest
                    self.wfile.write(b"event: changed\ndata: {}\n\n")
                    self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PORT", "8000")))
    # 0.0.0.0, not 127.0.0.1. A host binds its own loopback to nothing a user
    # can reach: the process starts, the health check times out, and the
    # platform kills it as unresponsive. Locally the two are the same.
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    args = ap.parse_args()
    ARTIFACTS.mkdir(exist_ok=True)

    _load_dotenv()
    try:
        from accel.server import db
        db.init()
        print("database ready")
    except Exception as exc:
        print(f"database unavailable, continuing: {type(exc).__name__}: {exc}")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ACCEL on http://{args.host}:{args.port}")
    srv.serve_forever()


def _load_dotenv() -> None:
    """Read .env when there is one. On a host the variables are already set."""
    f = SRC / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    main()
