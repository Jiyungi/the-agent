"""One Daytona sandbox, reused across states, producing Recordings.

The sandbox is remote compute. Weave tracing runs here, in the orchestrator,
never inside the sandbox: api.wandb.ai is not on Daytona's Tier 2 allowlist, so
a trace written from in there would silently fail.

Screenshot bytes cross into `ally.storage.save_screenshot` and are never seen
again. What travels on is the ref it returned.
"""

from __future__ import annotations

import io
import os
import json
import time
import base64
import concurrent.futures as _futures

from ally import storage
from .browser import CHROME_FLAGS, STATES, build_runner
from .recording import Candidate, Exclusion, Recording, Stop


class Session:
    """A started sandbox with Chromium and a virtual display."""

    def __init__(self, sandbox_id: str | None = None) -> None:
        from daytona import Daytona, DaytonaConfig

        self.client = Daytona(DaytonaConfig(
            api_key=os.environ["DAYTONA_API_KEY"],
            api_url=os.environ.get("DAYTONA_API_URL", "https://app.daytona.io/api"),
        ))
        if sandbox_id:
            self.sb = self.client.get(sandbox_id)
            if str(self.sb.state) != "SandboxState.STARTED":
                self.client.start(self.sb)
                self.sb = self.client.get(sandbox_id)
            self._owned = False
        else:
            from daytona import CreateSandboxFromSnapshotParams
            self.sb = self.client.create(CreateSandboxFromSnapshotParams(
                public=True, auto_stop_interval=30,
                labels={"project": "ally", "purpose": "audit"}))
            self._owned = True
        self.id = self.sb.id
        self._ready = False
        self._desktop = False

    # -- lifecycle --------------------------------------------------------

    def start_desktop(self, wait: int = 90) -> bool:
        """Boot the virtual display and noVNC, and wait for the port to answer.

        Nothing listens on 6080 until `computer_use.start()` has run, and the
        Daytona proxy answers a request for a dead port with a JSON 502 body.
        An iframe cannot tell that apart from a desktop -- it renders the JSON
        as text -- so the watch URL must not be handed out before the port is
        up. A run against BitEstate showed one live desktop beside three 502
        bodies for exactly this reason: lane 0 reused a warm sandbox and the
        other three were fresh.

        Returns False if the desktop never comes up, so the caller can say so
        rather than publishing a URL that will render an error.
        """
        if self._desktop:
            return True
        try:
            self.sb.computer_use.start()
        except Exception:
            pass
        probe = ("python3 -c \"import urllib.request as u; "
                 "print(u.urlopen('http://127.0.0.1:6080/vnc.html', "
                 "timeout=5).status)\" 2>/dev/null || echo down")
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                if "200" in (self.sb.process.exec(probe, timeout=30).result or ""):
                    self._desktop = True
                    return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def start_browser(self) -> None:
        if self._ready:
            return
        self.start_desktop()
        self.sb.process.exec(
            f"pkill -f chromium; sleep 2; DISPLAY=:0 nohup chromium {CHROME_FLAGS} "
            "about:blank > /tmp/chromium.log 2>&1 & echo ok", timeout=150)
        self.sb.process.exec("pip install --quiet websocket-client 2>&1 | tail -1; true",
                             timeout=240)  # noqa: E501
        time.sleep(3)
        self._ready = True

    def watch_url(self) -> str:
        """The live noVNC view. The port root serves a directory index; the
        viewer itself is /vnc.html."""
        return (self.sb.get_preview_link(6080).url.rstrip("/")
                + "/vnc.html?autoconnect=true&resize=scale")

    def close(self) -> None:
        if self._owned:
            try:
                self.client.delete(self.sb)
            except Exception:
                pass

    def exec(self, cmd: str, timeout: int = 300, tries: int = 4):
        """Run a command in the sandbox, retrying a dropped connection.

        A baseline drives six pages across four states, which is a few hundred
        round trips, and the Daytona connection drops occasionally with
        IncompleteRead. Without a retry, one dropped read ends a run that was
        twenty minutes in. The retry is on the transport, not on a command that
        ran and failed: a non-zero exit is returned to the caller as before.
        """
        import time as _t

        last = None
        for attempt in range(tries):
            try:
                # A wall-clock guard around the call, not just the sandbox-side
                # timeout. A run hung for twenty minutes on a clone that had
                # already finished: the command completed inside the sandbox and
                # the HTTP response never arrived, and `timeout` is what the
                # sandbox applies to the command, not what the client waits for.
                with _futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(self.sb.process.exec, cmd, timeout=timeout)
                    try:
                        return fut.result(timeout=timeout + 90)
                    except _futures.TimeoutError:
                        # The pool would block on exit waiting for the thread,
                        # so let it go and count this as a dropped connection.
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise TimeoutError(
                            f"no answer from the sandbox after {timeout + 90}s")
            except Exception as exc:
                last = exc
                text = f"{type(exc).__name__}: {exc}"
                transient = any(w in text for w in (
                    "no answer from the sandbox",
                    "IncompleteRead", "Connection broken", "RemoteDisconnected",
                    "Connection aborted", "timed out", "ConnectionResetError",
                    # local network, not the sandbox: DNS and pool exhaustion
                    "NameResolutionError", "getaddrinfo", "Max retries exceeded",
                    "Temporary failure in name resolution"))
                if not transient or attempt == tries - 1:
                    raise
                wait = 3 * (attempt + 1)
                print(f"    sandbox connection dropped ({type(exc).__name__}); "
                      f"retrying in {wait}s")
                _t.sleep(wait)
                try:            # the sandbox may have been stopped under us
                    if str(self.client.get(self.id).state) != "SandboxState.STARTED":
                        self.client.start(self.sb)
                        self._ready = False
                        self.start_browser()
                except Exception:
                    pass
        raise last

    # -- recording --------------------------------------------------------

    def restart_browser(self) -> None:
        """A pristine Chromium. Called before every recording.

        Determinism is not optional for a benchmark, and the browser carries
        state across navigations that changes what Tab reaches: the sequential
        focus navigation starting point, scroll position, and whatever the
        previous page's scripts left behind. Three recordings of the same page
        in one browser gave 11, 6 and 6 stops, with the second and third
        identical to each other -- the first run left something the rest
        inherited.

        Five seconds per state buys a recording that is the same every time,
        and a recall number that moves only when a check changes.
        """
        self.sb.process.exec(
            f"pkill -f chromium; sleep 1; DISPLAY=:0 nohup chromium {CHROME_FLAGS} "
            "about:blank > /tmp/chromium.log 2>&1 & echo ok", timeout=120)
        time.sleep(3.5)

    def record(self, url: str, state: str, run_id: str,
               max_tabs: int = 40, pair_capture: bool = True,
               shots: bool = True, no_exclusions: bool = False) -> Recording:
        """Drive one state and return its Recording."""
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}; known: {sorted(STATES)}")
        self.start_browser()
        self.restart_browser()

        self.sb.fs.upload_file(build_runner(url, state, max_tabs, pair_capture, shots,
                                            no_exclusions).encode(),
                               "/tmp/ally_record.py")
        res = self.exec("cd /tmp && python3 ally_record.py 2>&1 | tail -4", timeout=600)
        out = (res.result or "").strip()
        line = next((l for l in out.splitlines() if l.startswith("RESULT")), None)
        if not line:
            # A crashed recorder is not an unreached state, and must never be
            # returned as one. Folding the two together hid an IndentationError
            # in the remote script for four runs: every recording came back with
            # zero stops and zero candidates, which reads exactly like a page
            # that tabs nowhere. The distinction is the whole point of rule 7 --
            # a skip is reported, never silent -- so this raises.
            raise RuntimeError(
                f"the recorder produced no RESULT for {state} at {url}: {out[:400]}")
        raw = json.loads(line[len("RESULT "):])
        return self._assemble(raw, run_id)

    def _assemble(self, raw: dict, run_id: str) -> Recording:
        rec = Recording(
            url=raw["url"], state=raw["state"],
            state_reached=raw["state_reached"], reach_note=raw["reach_note"],
            truncated=raw.get("truncated", False),
            page_height=raw.get("page_height", 0),
            focusable_total=raw.get("focusable_total", 0),
            consent_note=raw.get("consent_note", "") or "",
            menu_triggers=int(raw.get("menu_triggers") or 0),
            dialog_triggers=int(raw.get("dialog_triggers") or 0),
            nav_error=raw.get("nav_error", "") or "",
            http_status=int(raw.get("http_status") or 0),
            text_length=int(raw.get("text_length") or 0),
            title=raw.get("title", "") or "",
        )
        for s in raw.get("stops", []):
            png = base64.b64decode(s.pop("png_focused") or "") or None
            blurred_b64 = s.pop("png_blurred", None)
            blurred = base64.b64decode(blurred_b64) if blurred_b64 else None
            ref = None
            if png:
                ref = storage.save_screenshot(run_id, rec.state, s["index"], png)
            stop = Stop(
                index=s["index"], tag=s["tag"], role=s.get("role"), name=s.get("name"),
                x=s["x"], y=s["y"], w=s["w"], h=s["h"], selector=s["selector"],
                vx=s.get("vx", s["x"]), vy=s.get("vy", s["y"]),
                screenshot=ref, obscured_by=s.get("obscured_by"),
                anchors=s.get("anchors") or {}, parent=s.get("parent"),
            )
            if blurred is not None and png is not None:
                stop.focus_delta = crop_diff(blurred, png, stop)
            rec.stops.append(stop)

        for e in raw.get("excluded", []):
            rec.excluded.append(Exclusion(
                selector=e.get("selector", ""), tag=e.get("tag", ""), role=e.get("role"),
                rule=e.get("rule", ""), reason=e.get("reason", ""),
                source=e.get("source", "")))

        for c in raw.get("candidates", []):
            rec.candidates.append(Candidate(
                selector=c["selector"], tag=c["tag"], role=c.get("role"),
                name=c.get("name"), x=c["x"], y=c["y"], w=c["w"], h=c["h"],
                focusable=bool(c.get("focusable")),
                sources=tuple(c.get("sources", ())),
                anchors=c.get("anchors") or {},
            ))
        return rec


def crop_diff(unfocused: bytes, focused: bytes, stop: Stop,
              pad: int = 8) -> float | None:
    """Fraction of pixels the focus indicator changed, around the element.

    The two frames are captured at the same scroll position, one with the
    element focused and one with focus dropped, so the only difference is the
    indicator itself. Cropping in viewport space is then straightforward:
    a screenshot is viewport-sized, and using document coordinates compared an
    unrelated region for anything below the fold.

    Returns None when the crop is empty or Pillow is missing, so the check
    reports not_evaluated rather than inventing a number.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return None
    try:
        a = Image.open(io.BytesIO(unfocused)).convert("RGB")
        b = Image.open(io.BytesIO(focused)).convert("RGB")
    except Exception:
        return None
    if a.size != b.size:
        return None

    left, top = max(0, stop.vx - pad), max(0, stop.vy - pad)
    right = min(a.width, stop.vx + stop.w + pad)
    bottom = min(a.height, stop.vy + stop.h + pad)
    if right <= left or bottom <= top:
        return None

    box = (int(left), int(top), int(right), int(bottom))
    diff = ImageChops.difference(a.crop(box), b.crop(box)).convert("L")
    total = diff.width * diff.height
    if total == 0:
        return None
    changed = sum(count for value, count in
                  zip(range(256), diff.histogram()) if value > 12)
    return changed / total
