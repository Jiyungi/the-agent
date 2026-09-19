"""Seam 2: the only place in the codebase that touches image bytes.

    save_screenshot(run_id, state, index, image_bytes) -> ref
    load_screenshot(ref) -> bytes

Local writes to disk and returns a relative path. The web version writes to R2
and returns a URL. Nothing above this file knows which, because a ref is opaque.

**Why bytes are confined here.** Our Weave ingestion cap is 1GB a month, and a
single benchmark run records a screenshot before and after every Tab press. Put
base64 into a Weave op input and the month is gone in one run. The rule is that
a Weave op input carries the *ref* this function returned, never the bytes.

A rule written in prose gets broken at 2am. A seam that owns the bytes cannot
be broken by accident: to log an image you would have to import PIL or open a
file yourself, which is visible in review. `load_screenshot` exists so that the
2.4.7 pixel comparison reads bytes back through the seam rather than reaching
for the filesystem.
"""

from __future__ import annotations

import re
import pathlib

from . import config

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(value: str) -> str:
    """Make a path segment safe. State names come from config, not users, but
    a state called 'dialog-open / step 2' should not escape its directory."""
    cleaned = _UNSAFE.sub("-", str(value)).strip("-.")
    return cleaned or "unnamed"


def screenshot_ref(run_id: str, state: str, index: int) -> str:
    """The ref for a screenshot, without writing anything.

    Relative to the root on purpose: an absolute path would not survive being
    written on one machine and read on another (seam 4).
    """
    return f"runs/{_slug(run_id)}/{_slug(state)}/stop-{int(index):03d}.png"


def save_screenshot(run_id: str, state: str, index: int, image_bytes: bytes) -> str:
    """Persist one screenshot. Returns the ref to record instead of the bytes."""
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise TypeError(
            f"save_screenshot expects raw bytes, got {type(image_bytes).__name__}. "
            "Decode base64 at the call site so only bytes cross this boundary."
        )
    ref = screenshot_ref(run_id, state, index)
    target = config.root() / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(image_bytes))
    return ref


def load_screenshot(ref: str) -> bytes:
    """Read a screenshot back. Used by the 2.4.7 pixel comparison.

    Exists so nothing outside this module opens an image file directly.
    """
    if _is_remote(ref):
        raise NotImplementedError(
            f"Remote refs are not readable locally yet: {ref!r}. "
            "Add the R2 fetch here when the web deployment lands."
        )
    target = config.root() / ref
    if not target.exists():
        raise FileNotFoundError(f"no screenshot at {ref!r}")
    return target.read_bytes()


def exists(ref: str) -> bool:
    if _is_remote(ref):
        return True
    return (config.root() / ref).exists()


def _is_remote(ref: str) -> bool:
    return ref.startswith(("http://", "https://"))
