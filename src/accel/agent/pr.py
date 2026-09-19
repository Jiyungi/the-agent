"""Branch, commit, push, pull request -- as the person who signed in.

The token is the GitHub token Supabase hands back when they authorise Accel,
so the work appears under their own account and touches only repositories they
already have access to. There is no `gh` CLI here and no machine account: the
previous shape depended on whoever ran the server being logged in locally,
which is fine on one laptop and meaningless once other people use it.

**A pull request is opened whether or not the fix could be verified.** The
alternative -- staying silent when the loop cannot prove a fix -- throws away
the diff, the evidence and the reasoning, and leaves the person with nothing
to look at. What changes with the outcome is the title and the first line of
the body, never whether the thing exists:

    verified      the re-audit confirmed the finding stopped firing
    unverified    a patch applied and built, the re-audit did not confirm it
    diagnosis     nothing could be patched; findings and evidence only

A reader can tell which they have from the title, and the body says what was
tested and what was not. That is the honest version of "always open a PR".
"""

from __future__ import annotations

import json
import shlex
import urllib.error
import urllib.request

API = "https://api.github.com"


def _api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json",
                 "User-Agent": "accel"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read() or b"{}")


def default_branch(owner: str, repo: str, token: str) -> str:
    try:
        return _api("GET", f"/repos/{owner}/{repo}", token).get("default_branch", "main")
    except Exception:
        return "main"


def build_body(audit, fix_summary: dict, outcomes: list, findings: list) -> tuple[str, str]:
    """(title, body). The title states the outcome; the body shows the work."""
    closed = fix_summary.get("closed", 0)
    created = fix_summary.get("created", 0)
    failed = [f for f in findings if getattr(f, "status", "") == "failed"]

    if closed:
        kind = "verified"
        headline = (f"Fixes {closed} keyboard accessibility "
                    f"{'issue' if closed == 1 else 'issues'}")
    elif outcomes:
        kind = "unverified"
        headline = "Proposed keyboard accessibility fixes (not confirmed)"
    else:
        kind = "diagnosis"
        headline = "Keyboard accessibility findings"

    lines = [f"## {headline}", ""]

    if kind == "verified":
        lines += [f"Each change below was applied, the project was rebuilt, and the same "
                  f"check was run again on the rebuilt page. **{closed} "
                  f"{'finding' if closed == 1 else 'findings'} stopped firing.**", ""]
        if created:
            lines += [f"⚠️ The patch also introduced **{created}** new "
                      f"{'finding' if created == 1 else 'findings'}. Net improvement: "
                      f"{closed - created}.", ""]
    elif kind == "unverified":
        lines += ["A patch was written and the project built, but the re-audit did **not** "
                  "confirm the finding stopped firing. The diff is here for review rather "
                  "than discarded — it is a proposal, not a proven fix.", ""]
    else:
        lines += ["Accel could not produce a safe patch for these, so this pull request "
                  "carries the evidence only. Nothing in the source is changed.", ""]

    lines += ["### What was found", ""]
    for f in failed[:12]:
        lines.append(f"- **{f.criterion}** on `{getattr(f, 'page', '') or audit.url}` "
                     f"({f.state}) — {(f.summary or f.reason or '')[:180]}")
    if len(failed) > 12:
        lines.append(f"- …and {len(failed) - 12} more")
    lines.append("")

    if outcomes:
        lines += ["### What was attempted", "",
                  "| Criterion | Component | Outcome | Patch attempts |",
                  "|---|---|---|---|"]
        for o in outcomes:
            lines.append(f"| {o.criterion} | `{o.component}` | {o.status} "
                         f"| {o.patch_attempts} |")
        lines.append("")

    lines += ["### How this was tested", "",
              "Accel opened each page in a real Chrome browser in an isolated cloud "
              "sandbox, pressed Tab through it, and recorded every focus stop with a "
              "screenshot and the accessibility tree. The criteria checked are "
              "2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 2.4.3 Focus Order, "
              "2.4.7 Focus Visible and 2.4.11 Focus Not Obscured — four of which have "
              "no axe-core rule, so a scanner cannot see them.", ""]

    ax = getattr(audit, "axe", None)
    if ax is not None and getattr(ax, "ran", False):
        lines.append(f"axe-core {ax.version} was run alongside on the same pages and "
                     f"fired {len(ax.violations)} rules.")
        lines.append("")

    lines.append("---")
    lines.append("*Opened by [Accel](https://the-agent-5dk9.onrender.com).*")
    return headline, "\n".join(lines)


def open_pull_request(session, owner: str, repo: str, token: str, job_id: str,
                      title: str, body: str, checkout: str,
                      base: str = "") -> dict:
    """Commit everything under `checkout`, push it, open the PR. Returns a dict.

    Never raises. A pull request that cannot be opened is a result to report,
    not an exception that loses the run -- the findings are still worth having.
    """
    branch = f"accel/{job_id}"
    base = base or default_branch(owner, repo, token)
    remote = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

    script = " && ".join([
        f"cd {shlex.quote(checkout)}",
        "git config user.email accel@users.noreply.github.com",
        "git config user.name Accel",
        f"git checkout -b {shlex.quote(branch)}",
        "git add -A",
        f"git diff --cached --quiet && echo NOCHANGES || git commit -q -m {shlex.quote(title)}",
        f"git remote set-url origin {shlex.quote(remote)}",
        f"git push -q -u origin {shlex.quote(branch)} 2>&1 && echo PUSHED",
    ])

    try:
        out = (session.sb.process.exec(script, timeout=300).result or "")
    except Exception as exc:
        return {"url": "", "error": f"push failed: {type(exc).__name__}: {exc}",
                "branch": branch}

    if "NOCHANGES" in out:
        # Nothing was patched. The findings still deserve a place to live, but
        # GitHub will not open a pull request with an empty diff, so say that
        # plainly rather than reporting a silent failure.
        return {"url": "", "branch": branch, "no_changes": True,
                "error": "no source changes to open a pull request with"}
    if "PUSHED" not in out:
        return {"url": "", "branch": branch,
                "error": "the branch could not be pushed", "log": out[-400:]}

    try:
        pr = _api("POST", f"/repos/{owner}/{repo}/pulls", token,
                  {"title": title, "head": branch, "base": base, "body": body})
        return {"url": pr.get("html_url", ""), "number": pr.get("number"),
                "branch": branch}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        return {"url": "", "branch": branch,
                "error": f"GitHub refused the pull request ({exc.code})",
                "log": detail}
    except Exception as exc:
        return {"url": "", "branch": branch,
                "error": f"{type(exc).__name__}: {exc}"}
