"""The breaker: plants defects and records what it planted.

One of three programs that share nothing. This one writes the broken pages and
a manifest. The agent never reads the manifest -- it gets a URL. The scorer
reads the manifest and the agent's findings and compares them.

Input:  a checkout of the clean-app branch (public/ is copied verbatim)
Output: broken-app/public/<criterion>.html, one criterion per page
        breaker/manifest.json, one row per planted instance

The manifest lives outside public/ so it is never served. If it were reachable
from the page, an agent that fetched it would score perfectly and we would
learn nothing.

Usage:
    python breaker/breaker.py <path-to-clean-checkout> [--out broken-app]
"""

from __future__ import annotations

import sys
import json
import shutil
import pathlib
import argparse
from dataclasses import asdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from defects import PAGES, FAKE_BUTTON_CSS, Defect  # noqa: E402

MARKER = "<!-- ally:injected -->"


def build_page(clean_html: str, page: str, defects: list[Defect]) -> str:
    """Apply one criterion's three defects to a copy of the clean page."""
    html = clean_html
    css_parts: list[str] = []
    js_parts: list[str] = []
    html_parts: list[str] = []

    for d in defects:
        for find, replace in d.replacements:
            if find not in html:
                raise SystemExit(
                    f"{page} instance {d.instance}: anchor not found in the clean page.\n"
                    f"  looking for: {find[:90]}\n"
                    "  The clean app changed. Fix the anchor rather than loosening it -- a "
                    "defect that silently fails to apply is a defect the scorer counts as "
                    "missed."
                )
            if html.count(find) != 1:
                raise SystemExit(
                    f"{page} instance {d.instance}: anchor matches {html.count(find)} times, "
                    "so the target element is ambiguous."
                )
            html = html.replace(find, replace)
        if d.css:
            css_parts.append(f"/* {d.criterion} instance {d.instance}: {d.region} */\n{d.css}")
        if d.js:
            js_parts.append(f"/* {d.criterion} instance {d.instance}: {d.region} */\n{d.js}")
        if d.html:
            html_parts.append(d.html)

    if any("fake-btn" in r for d in defects for _, r in d.replacements):
        css_parts.insert(0, FAKE_BUTTON_CSS.strip())

    block = [MARKER]
    if css_parts:
        block.append("<style>\n" + "\n\n".join(css_parts) + "\n</style>")
    if html_parts:
        block.extend(html_parts)
    if js_parts:
        # Defer to DOMContentLoaded so the APG scripts have bound first: a
        # capture-phase listener added before them would not be reached.
        body = "\n\n".join(js_parts)
        block.append(
            "<script>\ndocument.addEventListener('DOMContentLoaded', function () {\n"
            + body + "\n});\n</script>"
        )

    injected = "\n".join(block)
    if "</body>" not in html:
        raise SystemExit(f"{page}: the clean page has no </body> to inject before")
    return html.replace("</body>", injected + "\n</body>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clean", help="path to a checkout of the clean-app branch")
    ap.add_argument("--out", default="broken-app")
    args = ap.parse_args()

    clean_root = pathlib.Path(args.clean).resolve()
    clean_public = clean_root / "public"
    if not (clean_public / "index.html").exists():
        raise SystemExit(f"no public/index.html under {clean_root}")

    out_root = pathlib.Path(args.out).resolve()
    out_public = out_root / "public"
    if out_public.exists():
        shutil.rmtree(out_public)
    out_public.mkdir(parents=True)

    # Assets copied verbatim. The broken pages differ from the clean one only
    # by their planted defects.
    for sub in ("css", "js"):
        if (clean_public / sub).exists():
            shutil.copytree(clean_public / sub, out_public / sub)

    clean_html = (clean_public / "index.html").read_text(encoding="utf-8")
    manifest: list[dict] = []
    index_rows: list[str] = []

    for page, defects in PAGES.items():
        out_html = build_page(clean_html, page, defects)
        (out_public / f"{page}.html").write_text(out_html, encoding="utf-8")
        for d in defects:
            manifest.append(asdict(d) | {"page": f"{page}.html"})
        crit = defects[0].criterion
        index_rows.append(
            f'<li><a href="{page}.html">{page}.html</a> &mdash; '
            f"three instances of {crit}</li>"
        )
        print(f"  {page}.html  {len(defects)} defects  {len(out_html):,} bytes")

    # A plain index so the deployment has a root. It links the pages and says
    # nothing about what is wrong with them.
    (out_public / "index.html").write_text(
        "<!doctype html>\n<html lang=\"en\">\n<head><meta charset=\"utf-8\">\n"
        "<title>Ally broken pages</title>\n"
        '<link href="css/site.css" rel="stylesheet">\n</head>\n<body>\n'
        "<h1>Ally broken pages</h1>\n"
        "<p>Copies of the clean control page, one WCAG criterion damaged per page.</p>\n"
        "<ul>\n" + "\n".join(index_rows) + "\n</ul>\n</body>\n</html>\n",
        encoding="utf-8")

    manifest_path = pathlib.Path(__file__).resolve().parent / "manifest.json"
    manifest_path.write_text(json.dumps({
        "source": "clean-app branch, public/index.html",
        "pages": len(PAGES),
        "defects": len(manifest),
        "rows": manifest,
    }, indent=2), encoding="utf-8")

    print(f"\n{len(manifest)} defects across {len(PAGES)} pages")
    print(f"manifest: {manifest_path}")
    print(f"pages:    {out_public}")
    print("\nThe manifest is outside public/ deliberately. The agent gets a URL "
          "and nothing else.")


if __name__ == "__main__":
    main()
