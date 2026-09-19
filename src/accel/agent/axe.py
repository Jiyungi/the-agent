"""axe-core, run as-is, alongside our five.

We wrote none of it and we improve none of it. It reads one loaded page and
reports static problems; we take its output and put it in the report.

Two things this module is careful about:

  * **axe is bundled, never fetched from a CDN.** cdnjs and jsDelivr are both
    blocked by Daytona's Tier 2 egress allowlist, so a CDN fetch fails and the
    page is scanned by nothing. That failure used to be recorded as a clean
    page, which is the worst result an accessibility tool can produce.
  * **`ran` is reported separately from the violation list.** An empty list
    means "clean page" or "axe never executed" and those are not the same fact.
    Only a completed run sets `ran`.

The comparison claim depends on `overlapping()` staying empty: no rule axe
fires may carry a WCAG tag matching one of our five.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: WCAG tags axe uses for the five criteria we own. If a rule axe fires carries
#: one of these, we are competing with axe on its own ground and the scope says
#: not to.
OUR_TAGS = {
    "wcag211": "2.1.1", "wcag212": "2.1.2", "wcag243": "2.4.3",
    "wcag247": "2.4.7", "wcag2411": "2.4.11",
}

AXE_VERSION = "4.13.0"


@dataclass
class AxeResult:
    ran: bool
    reason: str = ""
    violations: list[dict] = field(default_factory=list)
    version: str = ""

    @property
    def criteria(self) -> set[str]:
        """The WCAG criteria axe actually fired on, from its own tags."""
        out: set[str] = set()
        for v in self.violations:
            for t in v.get("tags", []):
                if t.startswith("wcag") and t[4:].isdigit():
                    digits = t[4:]
                    # wcag211 -> 2.1.1, wcag2411 -> 2.4.11
                    if len(digits) == 3:
                        out.add(f"{digits[0]}.{digits[1]}.{digits[2]}")
                    elif len(digits) == 4:
                        out.add(f"{digits[0]}.{digits[1]}.{digits[2:]}")
        return out

    def overlapping(self) -> list[str]:
        """Rules axe fired that are tagged to one of our five."""
        hits = []
        for v in self.violations:
            for t in v.get("tags", []):
                if t in OUR_TAGS:
                    hits.append(f"{v['id']} ({OUR_TAGS[t]})")
        return sorted(set(hits))


INSTALL = (
    "test -f /tmp/axe.min.js || (cd /tmp && npm install --silent --no-fund "
    "--no-audit axe-core@__VERSION__ >/dev/null 2>&1 && "
    "cp /tmp/node_modules/axe-core/axe.min.js /tmp/axe.min.js); "
    "test -f /tmp/axe.min.js && echo READY || echo MISSING"
)

RUNNER = r'''
import json, time, urllib.request
from websocket import create_connection
URL = __URL__
try:
    axe_src = open("/tmp/axe.min.js").read()
except OSError as e:
    print("RESULT " + json.dumps({"ran": False, "reason": "axe-core not bundled: %s" % e}))
    raise SystemExit(0)

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
page = next(t for t in tabs if t["type"] == "page")
ws = create_connection(page["webSocketDebuggerUrl"], timeout=60, max_size=60000000)
mid = 0
def send(m, p=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == mid:
            return r

send("Page.navigate", {"url": URL}); time.sleep(4)
send("Runtime.evaluate", {"expression": axe_src})
# The page callback reports whether axe.run actually completed. A source that
# never defines window.axe resolves with no violations, and that must not be
# reported as a clean page.
r = send("Runtime.evaluate", {"awaitPromise": True, "returnByValue": True, "expression":
  """(function(){
       if (!window.axe || typeof window.axe.run !== 'function')
         return Promise.resolve(JSON.stringify({ran:false, reason:'window.axe.run never defined'}));
       return axe.run(document, {resultTypes:['violations'],
         runOnly:{type:'tag', values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']}})
         .then(function(x){ return JSON.stringify({ran:true, version:(axe.version||''),
           violations:x.violations.map(function(v){
             return {id:v.id, impact:v.impact, tags:v.tags, help:v.help,
                     nodes:v.nodes.length,
                     targets:v.nodes.slice(0,5).map(function(n){return String(n.target);})};})});})
         .catch(function(e){ return JSON.stringify({ran:false, reason:String(e)}); });
     })()"""})
print("RESULT " + (r.get("result", {}).get("result", {}).get("value")
                   or json.dumps({"ran": False, "reason": "no response from axe"})))
ws.close()
'''


def run_axe(session, url: str) -> AxeResult:
    """Scan one URL with the bundled axe-core inside the session's sandbox."""
    session.start_browser()
    check = session.exec(INSTALL.replace("__VERSION__", AXE_VERSION), timeout=420)
    if "READY" not in (check.result or ""):
        return AxeResult(ran=False, reason="axe-core could not be bundled into the sandbox")

    session.sb.fs.upload_file(RUNNER.replace("__URL__", json.dumps(url)).encode(),
                              "/tmp/ally_axe.py")
    res = session.exec("cd /tmp && python3 ally_axe.py 2>&1 | tail -3", timeout=420)
    out = (res.result or "").strip()
    line = next((l for l in out.splitlines() if l.startswith("RESULT")), None)
    if not line:
        return AxeResult(ran=False, reason=f"the axe runner produced no result: {out[:160]}")
    data = json.loads(line[len("RESULT "):])
    return AxeResult(ran=bool(data.get("ran")), reason=data.get("reason", ""),
                     violations=data.get("violations", []), version=data.get("version", ""))
