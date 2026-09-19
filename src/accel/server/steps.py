"""Find the steps of a single-page flow, so each one can be audited on its own.

Clearway's repository has one route, `app/page.tsx`. Everything a visitor calls
a page -- pick a language, answer a question, review the packet -- is a state of
that one page, reached by pressing a button. Crawling `a[href]` finds nothing,
which is why a run reported "1 page" on an app that plainly has more than one
screen.

So the unit of work is a STEP: open the page, press one control, and audit what
comes up. Each step gets its own sandbox, so the states cannot contaminate each
other and every one has a desktop to watch.

A control counts as a step when pressing it changes what the page says. That is
checked rather than assumed: the assertion is the page's own visible text, read
before and after, and a control that changes nothing is reported as not reached
instead of being audited twice under different names.
"""

from __future__ import annotations

import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))



def _op(fn):
    return fn


#: Controls that take the visitor somewhere, in the order the page presents
#: them. Skips anything that obviously does not advance a flow.
FIND_STEPS_JS = r"""
(function () {
  function sel(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    var parts = [];
    for (var n = el; n && n.nodeType === 1 && n !== document.documentElement;
         n = n.parentElement) {
      if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
      var part = n.tagName.toLowerCase(), p = n.parentElement;
      if (p) {
        var same = Array.prototype.filter.call(p.children, function (c) {
          return c.tagName === n.tagName; });
        if (same.length > 1) {
          part += ':nth-of-type(' + (Array.prototype.indexOf.call(same, n) + 1) + ')';
        }
      }
      parts.unshift(part);
    }
    return parts.join('>');
  }
  var SKIP = /^(close|dismiss|cancel|back|reload|skip|accept all|reject all|cookie)/i;
  var out = [];
  var nodes = document.querySelectorAll(
    'button:not([disabled]), [role="button"], a[href]:not([href^="#"])');
  for (var i = 0; i < nodes.length && out.length < 12; i++) {
    var el = nodes[i];
    var r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    var cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    var label = (el.innerText || el.getAttribute('aria-label') || '').trim()
                  .replace(/\s+/g, ' ').slice(0, 40);
    if (!label) continue;
    if (SKIP.test(label)) continue;
    out.push({ label: label, selector: sel(el) });
  }
  return out;
})()
"""


def discover_steps(session, url: str, limit: int = 3) -> list:
    """The first `limit` controls on the page that look like they advance it.

    Returns [{label, selector}]. Empty when the page has no such control, which
    is a real answer: a page with one screen has one step.
    """
    body = [
        "import json, time, urllib.request",
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
        "time.sleep(8)",
        "r = send('Runtime.evaluate', {'expression': " + repr(FIND_STEPS_JS)
        + ", 'returnByValue': True})",
        "print('STEPS ' + json.dumps(r.get('result', {}).get('result', {}).get('value') or []))",
        "ws.close()",
    ]
    session.sb.fs.upload_file(chr(10).join(body).encode(), "/tmp/ally_steps.py")
    out = session.exec("cd /tmp && python3 ally_steps.py 2>&1 | tail -3", timeout=240)
    line = next((l for l in (out.result or "").splitlines() if l.startswith("STEPS")), None)
    if not line:
        return []
    try:
        found = json.loads(line[len("STEPS "):])
    except Exception:
        return []
    # One label, one step. A run against touchgrass spent two of its four
    # sandboxes pressing two different controls both labelled "Sign up", and
    # reported the same screen twice under the same name.
    seen, out = set(), []
    for step in found:
        label = (step.get("label") or "").strip().lower()
        if label in seen:
            continue
        seen.add(label)
        out.append(step)
        if len(out) >= limit:
            break
    return out


def register_step_state(name: str, selector: str) -> str:
    """Teach the recorder one more state: press this control, then record.

    The assertion is the page's own visible text. A control that leaves the text
    unchanged did not take the visitor anywhere, and the state is reported as not
    reached rather than audited as if it were somewhere new.
    """
    from accel.agent.browser import STATES

    STATES[name] = {
        "reach": ("(function(){var e=document.querySelector(" + json.dumps(selector)
                  + "); if(e){e.click(); return 1;} return 0;})()"),
        "assert": "(document.body ? document.body.innerText.slice(0, 220) : '')",
        "expect_change": True,
    }
    return name
