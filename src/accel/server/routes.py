"""Ask the repository what pages the site has, instead of crawling for them.

Crawling `a[href]` only ever finds what the entry page chooses to link. On
BitEstate the router declares ten routes and the landing page links to two of
them; the other eight are behind a login, so a crawler reports a ten-page app as
a two-page one. The repository has no such blind spot: a route is written down
in the source whether or not anything links to it yet.

Four conventions, because four is what these projects actually use:

  Next.js App Router    app/**/page.tsx          -> /**
  Next.js Pages Router  pages/**.tsx             -> /**
  Client-side router    path="/thing" in src/**  -> /thing
  Plain static          **/*.html                -> /**.html

Dynamic segments are skipped. `app/property/[id]/page.tsx` and `path="/p/:id"`
name a shape, not a page, and guessing an id produces a 404 that would then be
audited as if it were a screen.
"""

from __future__ import annotations

import re
import sys
import pathlib
from urllib.parse import urljoin, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))



def _op(fn):
    return fn


#: One <Route ...> element, however many lines it spans. The /login route in
#: BitEstate is written across eight lines, so a line-based grep misses it.
ROUTE = re.compile(r"<Route\b(.*?)(?:/>|>)", re.S)
#: The path it serves, and the component it renders.
ROUTE_PATH = re.compile(r"""path=["']([^"']+)["']""")
ROUTE_ELEMENT = re.compile(r"element=\{\s*<\s*([A-Za-z0-9_]+)")

#: A path with a dynamic segment names a shape, not a page.
DYNAMIC = re.compile(r"(\[[^\]]+\]|:[A-Za-z_]|\*|\.\.\.)")
#: Route groups and private folders are organisation, not URL.
GROUP = re.compile(r"^[\(_]")


def _from_app_router(files: list[str]) -> list[str]:
    out = []
    for f in files:
        m = re.match(r"^(?:src/)?app/(.*)page\.(tsx|jsx|ts|js)$", f)
        if not m:
            continue
        segs = [s for s in m.group(1).strip("/").split("/") if s]
        if any(GROUP.match(s) for s in segs):
            segs = [s for s in segs if not GROUP.match(s)]
        route = "/" + "/".join(segs)
        if DYNAMIC.search(route):
            continue
        out.append(route.rstrip("/") or "/")
    return out


def _from_pages_router(files: list[str]) -> list[str]:
    out = []
    for f in files:
        m = re.match(r"^(?:src/)?pages/(.+)\.(tsx|jsx|ts|js)$", f)
        if not m:
            continue
        p = m.group(1)
        if p.startswith("_") or p.startswith("api/"):
            continue
        route = "/" + ("" if p == "index" else p.removesuffix("/index"))
        if DYNAMIC.search(route):
            continue
        out.append(route.rstrip("/") or "/")
    return out


def _from_html(files: list[str]) -> list[str]:
    out = []
    for f in files:
        if not f.lower().endswith((".html", ".htm")):
            continue
        # Strip a web-root prefix: public/about.html is served at /about.html.
        p = re.sub(r"^(public|static|dist|site|www|build)/", "", f)
        out.append("/" + ("" if p.lower() in ("index.html", "index.htm") else p))
    return out


def _router_routes(session, checkout: str) -> list[tuple[str, str]]:
    """(route, component) for every real page a client-side router declares.

    A <Navigate> route is a redirect, not a page. Auditing one drives the
    browser to somewhere already being audited and reports the answer twice
    under a name nobody can act on: BitEstate spent two of its four sandboxes
    on /audit-trail and /list-property, which are redirects to /verify and
    /source-truth.

    The component is carried along because the route is the only thing that
    knows which file draws the page, and that is the file a patch must edit.
    """
    found = session.exec(
        f"cd {checkout} && grep -rl '<Routes' src app 2>/dev/null | head -1",
        timeout=120)
    path = (found.result or "").strip().splitlines()
    if not path:
        return []
    text = session.exec(f"cd {checkout} && sed -n '1,600p' {path[0]}", timeout=120)
    body = text.result or ""

    out: list[tuple[str, str]] = []
    for attrs in ROUTE.findall(body):
        m = ROUTE_PATH.search(attrs)
        if not m:
            continue
        route = m.group(1)
        if not route.startswith("/") or DYNAMIC.search(route):
            continue
        el = ROUTE_ELEMENT.search(attrs)
        component = el.group(1) if el else ""
        if component == "Navigate":
            continue                      # a redirect, not a page
        out.append((route.rstrip("/") or "/", component))
    return out


def source_for_route(session, checkout: str, component: str) -> str:
    """The file that draws `component`, or "" when it cannot be found.

    A run against BitEstate reported every page as backed by index.html,
    because that is the only HTML file in a Parcel project. index.html is seven
    lines long and contains no control a patch could change.
    """
    if not component:
        return ""
    hit = session.exec(
        f"cd {checkout} && git ls-files | grep -iE '(^|/){component}\\.(jsx|tsx|js|ts)$' "
        f"| head -1", timeout=120)
    return (hit.result or "").strip().splitlines()[0] if (hit.result or "").strip() else ""


def routes_from_repo(session, checkout: str, base_url: str,
                     limit: int = 8) -> list[str]:
    """Absolute URLs for the pages this repository declares.

    The entry URL always comes first, then the rest in the order the source
    lists them, deduplicated against it.
    """
    listing = session.exec(
        f"cd {checkout} && git ls-files | head -3000", timeout=180)
    files = [f.strip() for f in (listing.result or "").splitlines() if f.strip()]
    if not files:
        return []

    routes = _from_app_router(files) + _from_pages_router(files) + _from_html(files)

    # A client-side router writes its routes in source rather than in the file
    # tree, so read them out of it. BitEstate is one: ten routes in the config,
    # one index.html on disk.
    if len(routes) <= 1:
        # Two routes that render the same component are one screen. BitEstate
        # serves HomePage at both "/" and "/home", and a sandbox spent on the
        # second one audits a page already being audited. The duplicate is
        # caught after the fact by comparing renders; catching it here costs
        # nothing and saves the sandbox.
        seen_components: set = set()
        for route, component in _router_routes(session, checkout):
            if component and component in seen_components:
                continue
            if component:
                seen_components.add(component)
            routes.append(route)

    base = base_url.rstrip("/")
    origin = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    seen, out = set(), []
    for route in routes:
        url = urljoin(origin + "/", route.lstrip("/"))
        url = url.rstrip("/") or origin
        if url in seen:
            continue
        seen.add(url)
        out.append(url)

    # The URL the user gave leads, whatever the source order says.
    entry = base or origin
    out = [entry] + [u for u in out if u.rstrip("/") != entry.rstrip("/")]
    return out[:limit]


def sources_by_page(session, checkout: str, base_url: str) -> dict:
    """{page url: source file} for every route the router declares.

    The fix loop patches per page, so it needs the file behind each page and
    not just the one behind the entry URL.
    """
    from urllib.parse import urljoin, urlparse

    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    out: dict = {}
    for route, component in _router_routes(session, checkout):
        src = source_for_route(session, checkout, component)
        if not src:
            continue
        url = urljoin(origin + "/", route.lstrip("/")).rstrip("/") or origin
        out[url] = src
    return out
