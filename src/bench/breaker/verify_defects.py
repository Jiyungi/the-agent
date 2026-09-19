"""Does each planted defect actually manifest in a browser?

A defect that silently fails to apply is counted as "missed" by the scorer, so
the baseline would blame the checker for a fixture bug. This asserts each of the
fifteen is really present on the deployed page, using the browser rather than
the source text.

This is the breaker checking its own work. It is not the scorer and it does not
look at any finding.

Usage:  python breaker/verify_defects.py <sandbox-id> [base-url]
"""

from __future__ import annotations

import os
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import wb_env  # noqa: E402

wb_env.load_dotenv()
wb_env.use_certifi_bundle()

from daytona import Daytona, DaytonaConfig  # noqa: E402

SANDBOX_ID = sys.argv[1]
BASE = (sys.argv[2] if len(sys.argv) > 2 else "https://broken-app.vercel.app").rstrip("/")

# One assertion per planted instance, evaluated in the page. Each returns true
# only when the damage is really there.
ASSERTS = {
    ("2-1-1", 1): "var e=document.getElementById('ally-d1');"
                  "return !!e && e.tagName==='DIV' && e.tabIndex < 0;",
    ("2-1-1", 2): "var e=document.getElementById('ally-d2');"
                  "return !!e && e.tagName==='DIV' && e.tabIndex < 0;",
    ("2-1-1", 3): "var e=document.querySelector('[role=\"switch\"]');"
                  "return !!e && !e.hasAttribute('tabindex');",
    # For the trap instances, press Tab for real and see whether focus moved.
    ("2-1-2", 1): "return window.__allyTrap === 'menu1';",
    ("2-1-2", 2): "return window.__allyTrap === 'tablist';",
    ("2-1-2", 3): "return window.__allyTrap === 'email';",
    ("2-4-3", 1): "var f=getComputedStyle(document.getElementById('signup'));"
                  "var i=getComputedStyle(document.getElementById('email_item'));"
                  "return f.display==='flex' && i.order==='-1';",
    ("2-4-3", 2): "var e=document.querySelector('.dialog_form_actions');"
                  "return !!e && getComputedStyle(e).flexDirection==='row-reverse';",
    ("2-4-3", 3): "var e=document.querySelector('[role=\"tablist\"]');"
                  "return !!e && getComputedStyle(e).flexDirection==='row-reverse';",
    # 2.4.7 is NOT verified here. It used to be, by focusing the control and
    # reading back outlineStyle, which broke two verification rules at once:
    # rule 2, because applying `outline: none` and asserting the outline is
    # none is a mirror rather than a test, and rule 4, because element.focus()
    # is not keyboard focus and the browser does not paint the indicator for
    # it. It passed while the switch defect was entirely absent.
    # breaker/verify_focus_visible.py does it properly: real Tab presses, real
    # screenshots, against the clean page as well as the broken one.
    # A cover really obscures only if it is the element painted at that point.
    ("2-4-11", 1): "var c=document.getElementById('ally-cover-1'); if(!c) return false;"
                   "var r=c.getBoundingClientRect();"
                   "return document.elementFromPoint(r.left+40, r.top+20)===c;",
    ("2-4-11", 2): "var c=document.getElementById('ally-cover-2'); if(!c) return false;"
                   "var r=c.getBoundingClientRect();"
                   "return document.elementFromPoint(r.left+40, r.top+20)===c;",
    ("2-4-11", 3): "var c=document.getElementById('ally-cover-3'); if(!c) return false;"
                   "var r=c.getBoundingClientRect();"
                   "return document.elementFromPoint(r.left+40, r.top+20)===c;",
}

# The trap instances need a real Tab press in the right state, so they get a
# bespoke probe that sets window.__allyTrap when focus fails to move.
TRAP_PROBE = {
    "menu1": "document.getElementById('menubutton1').click();",
    "tablist": "document.getElementById('tab-1').focus();",
    "email": "document.getElementById('email').focus();",
}

RUNNER = r'''
import json, time, urllib.request
from websocket import create_connection
BASE = "__BASE__"
ASSERTS = __ASSERTS__
TRAPS = __TRAPS__

t = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
p = next(x for x in t if x["type"] == "page")
ws = create_connection(p["webSocketDebuggerUrl"], timeout=40)
mid = 0
def s(m, pa=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": m, "params": pa or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == mid:
            return r
def ev(expr, wrap=True):
    e = "(function(){%s})()" % expr if wrap else expr
    r = s("Runtime.evaluate", {"expression": e, "returnByValue": True})
    return r.get("result", {}).get("result", {}).get("value")
def tab():
    for ty in ("rawKeyDown", "keyUp"):
        s("Input.dispatchKeyEvent", {"type": ty, "code": "Tab", "key": "Tab",
                                     "windowsVirtualKeyCode": 9})

out = {}
for page in ["2-1-1", "2-1-2", "2-4-3", "2-4-7", "2-4-11"]:
    s("Page.navigate", {"url": BASE + "/" + page + ".html"}); time.sleep(3.5)
    if page == "2-1-2":
        # reach each container, press Tab, see whether focus actually moved
        for name, reach in TRAPS.items():
            s("Page.navigate", {"url": BASE + "/" + page + ".html"}); time.sleep(3)
            ev(reach, wrap=False); time.sleep(0.8)
            before = ev("var a=document.activeElement; return (a.id||a.tagName)+'|'+(a.innerText||'').slice(0,18);")
            tab(); time.sleep(0.5)
            after = ev("var a=document.activeElement; return (a.id||a.tagName)+'|'+(a.innerText||'').slice(0,18);")
            ev("window.__allyTrap = %r;" % name if before == after else "window.__allyTrap=null;", wrap=False)
            out[page + ":" + name] = {"trapped": before == after, "before": before, "after": after}
        continue
    if page == "2-4-3":
        ev("var b=document.querySelector(\"[onclick*='openDialog']\")||document.getElementById('ally-d1'); if(b) b.click();", wrap=False)
        time.sleep(0.8)
    for (pg, inst), expr in ASSERTS.items():
        if pg != page:
            continue
        out[pg + ":" + str(inst)] = {"present": bool(ev(expr))}

print("RESULT " + json.dumps(out))
ws.close()
'''


def main() -> None:
    d = Daytona(DaytonaConfig(
        api_key=os.environ["DAYTONA_API_KEY"],
        api_url=os.environ.get("DAYTONA_API_URL", "https://app.daytona.io/api"),
    ))
    sb = d.get(SANDBOX_ID)
    if str(sb.state) != "SandboxState.STARTED":
        d.start(sb)
        sb = d.get(SANDBOX_ID)

    asserts = {f"{p}:{i}": e for (p, i), e in ASSERTS.items()}
    runner = (RUNNER.replace("__BASE__", BASE)
              .replace("__ASSERTS__", repr([[k.split(":")[0], int(k.split(":")[1]), v]
                                            for k, v in asserts.items()]))
              .replace("__TRAPS__", json.dumps(TRAP_PROBE)))
    # rebuild ASSERTS in the runner's own shape
    runner = runner.replace(
        "for (pg, inst), expr in ASSERTS.items():",
        "for pg, inst, expr in ASSERTS:")
    sb.fs.upload_file(runner.encode(), "/tmp/verify.py")
    sb.process.exec("pkill -f chromium; sleep 2; DISPLAY=:0 nohup chromium --no-sandbox "
                    "--disable-dev-shm-usage --disable-gpu --no-first-run "
                    "--remote-debugging-port=9222 --remote-allow-origins=* "
                    "--window-size=1024,740 about:blank > /tmp/c.log 2>&1 & echo ok", timeout=120)
    r = sb.process.exec("cd /tmp && python3 verify.py 2>&1 | tail -3", timeout=600)
    out = (r.result or "").strip()
    line = next((l for l in out.splitlines() if l.startswith("RESULT")), None)
    if not line:
        print("no result:\n", out[:900])
        raise SystemExit(1)
    res = json.loads(line[7:])

    manifest = json.loads((pathlib.Path(__file__).resolve().parent / "manifest.json")
                          .read_text(encoding="utf-8"))
    # 2.4.7 is measured by verify_focus_visible.py, not here.
    manifest["rows"] = [r for r in manifest["rows"] if r["criterion"] != "2.4.7"]
    trap_names = {1: "menu1", 2: "tablist", 3: "email"}
    ok = bad = 0
    print(f"{'criterion':10s} {'inst':>4s}  {'present':>8s}  region")
    print("-" * 74)
    for row in manifest["rows"]:
        page = row["page"].replace(".html", "")
        if row["criterion"] == "2.1.2":
            key = f"{page}:{trap_names[row['instance']]}"
            present = res.get(key, {}).get("trapped", False)
            extra = f"  focus {res.get(key, {}).get('before')} -> {res.get(key, {}).get('after')}"
        else:
            key = f"{page}:{row['instance']}"
            present = res.get(key, {}).get("present", False)
            extra = ""
        ok += present
        bad += not present
        print(f"{row['criterion']:10s} {row['instance']:>4d}  "
              f"{'YES' if present else 'NOT APPLIED':>8s}  {row['region']}{extra}")
    print("-" * 74)
    print(f"{ok}/{ok + bad} defects verified present in a browser "
          "(2.4.7 is covered by verify_focus_visible.py)")
    if bad:
        print("\nA defect that is not present would be scored as missed, which blames the "
              "checker for a fixture bug. Fix before running the baseline.")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
