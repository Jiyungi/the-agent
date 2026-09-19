"""Serve the live browser view from our own origin.

Daytona puts an interstitial in front of every preview URL -- "You are about to
visit ... Be careful about disclosing personal or financial information" -- and
it cannot be clicked away once, because every sandbox gets its own subdomain
and the dismissal cookie is per-origin. Four new subdomains per run means four
warnings per run, in front of the thing the product is built to show.

Daytona's documented way out is the header `X-Daytona-Skip-Preview-Warning`,
which an <iframe> cannot send. Signed preview URLs carry the token in the
hostname instead, but the proxy's certificate does not cover that form here, so
the browser rejects them before the request is made.

So the browser never talks to Daytona. It talks to us, at

    /watch/<sandbox-id>/vnc.html

and this module relays to the sandbox with the headers the docs ask for. That
is Daytona's third suggestion -- a custom preview proxy -- and it is about a
hundred lines because noVNC needs two kinds of relay:

  * **HTTP** for the page, its scripts and its images. Ordinary request in,
    ordinary response out.
  * **WebSocket** for the frames themselves. The upgrade is performed against
    the sandbox, and then the two sockets are joined and bytes are copied in
    both directions until one of them closes. Nothing here parses a WebSocket
    frame; it does not need to know what the bytes mean to carry them.
"""

from __future__ import annotations

import select
import socket
import ssl
import threading
import urllib.error
import urllib.request

#: The headers that get us past the interstitial. `x-daytona-preview-token`
#: authorises the request; the skip header suppresses the warning page.
def _headers(token: str) -> dict:
    h = {"X-Daytona-Skip-Preview-Warning": "true",
         "User-Agent": "accel-watch"}
    if token:
        h["x-daytona-preview-token"] = token
    return h


_LINKS: dict[str, tuple[str, str]] = {}
_LOCK = threading.Lock()


def remember(sandbox_id: str, url: str, token: str) -> str:
    """Record a sandbox's preview target and return the path to watch it at.

    The token never reaches the browser. The page is handed a path on our own
    origin and this process holds the credential, which is the point of
    proxying rather than redirecting.
    """
    with _LOCK:
        _LINKS[sandbox_id] = (url.rstrip("/"), token or "")
    return f"/watch/{sandbox_id}/vnc.html?autoconnect=true&resize=scale"


def target_for(sandbox_id: str) -> tuple[str, str] | None:
    with _LOCK:
        return _LINKS.get(sandbox_id)


def split_path(path: str) -> tuple[str, str]:
    """`/watch/<id>/rest?query` -> (id, 'rest?query')."""
    rest = path[len("/watch/"):]
    sid, _, tail = rest.partition("/")
    return sid, tail


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(sandbox_id: str, tail: str) -> tuple[int, str, bytes]:
    """(status, content type, body) for one proxied GET."""
    found = target_for(sandbox_id)
    if not found:
        return 404, "text/plain", b"no such sandbox in this session"
    base, token = found
    req = urllib.request.Request(f"{base}/{tail.lstrip('/')}",
                                 headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return (r.status,
                    r.headers.get("Content-Type", "application/octet-stream"),
                    r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, "text/plain", exc.read()[:2000]
    except Exception as exc:
        return 502, "text/plain", f"{type(exc).__name__}: {exc}".encode()


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------

def _pump(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes between two sockets until either end goes quiet."""
    socks = [a, b]
    try:
        while True:
            ready, _, bad = select.select(socks, [], socks, 60)
            if bad:
                return
            if not ready:
                return                      # idle for a minute; let it go
            for s in ready:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


def relay(handler, sandbox_id: str, tail: str) -> bool:
    """Join the browser's socket to the sandbox's. True if it was handled.

    The client's upgrade request is replayed against the sandbox with the
    Daytona headers added, the sandbox's 101 is written back verbatim, and from
    there the two are simply joined.
    """
    found = target_for(sandbox_id)
    if not found:
        return False
    base, token = found
    host = base.split("://", 1)[-1].split("/", 1)[0]

    try:
        raw = socket.create_connection((host, 443), timeout=30)
        upstream = ssl.create_default_context().wrap_socket(
            raw, server_hostname=host)
    except Exception:
        return False

    # Replay the handshake, keeping the client's key and version so the
    # sandbox's answer is one the browser will accept, and adding ours.
    lines = [f"GET /{tail.lstrip('/')} HTTP/1.1", f"Host: {host}"]
    for name in ("Sec-WebSocket-Key", "Sec-WebSocket-Version",
                 "Sec-WebSocket-Protocol", "Sec-WebSocket-Extensions",
                 "Origin"):
        value = handler.headers.get(name)
        if value:
            lines.append(f"{name}: {value}")
    lines += ["Upgrade: websocket", "Connection: Upgrade"]
    for k, v in _headers(token).items():
        lines.append(f"{k}: {v}")
    try:
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    except OSError:
        upstream.close()
        return False

    # Read just the status line and headers; anything after the blank line is
    # already frame data and belongs to the browser.
    buf = b""
    try:
        while b"\r\n\r\n" not in buf:
            chunk = upstream.recv(4096)
            if not chunk:
                upstream.close()
                return False
            buf += chunk
    except OSError:
        upstream.close()
        return False

    head, _, leftover = buf.partition(b"\r\n\r\n")
    if b"101" not in head.split(b"\r\n", 1)[0]:
        upstream.close()
        return False

    client = handler.connection
    try:
        client.sendall(head + b"\r\n\r\n")
        if leftover:
            client.sendall(leftover)
    except OSError:
        upstream.close()
        return False

    _pump(client, upstream)
    return True
