"""Seam 3: every git and GitHub API call gets its credential from here.

    get_github_token(repo) -> token

Local resolves it from the environment, falling back to the `gh` CLI. The web
version will fetch a GitHub App installation token for that repository. Every
git operation after this call is identical, which is the point: `repo` is taken
now even though the local path ignores it, because the installation token is
per-repository and adding the argument later would touch every call site.

Verified 2026-09-12: the `gh` CLI token carries `repo` scope and admin, maintain
and push on Carldtitan/Ally, and authenticates `git ls-remote` over HTTPS. So
there is nothing to provision locally.

Tokens are never logged, never put in a Weave op input, and never interpolated
into a command string. `authenticated_remote` builds the push URL, and callers
should keep it out of anything that gets printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


class TokenError(RuntimeError):
    """No usable credential. Raised loudly at startup rather than at push time."""


@lru_cache(maxsize=8)
def get_github_token(repo: str) -> str:
    """A token that can push to `repo` and open pull requests on it.

    `repo` is "owner/name". Unused locally; required by the web version.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()

    gh = shutil.which("gh")
    if gh:
        try:
            out = subprocess.run(
                [gh, "auth", "token"],
                capture_output=True, text=True, timeout=30, check=True,
            )
            token = (out.stdout or "").strip()
            if token:
                return token
        except (subprocess.SubprocessError, OSError):
            pass

    raise TokenError(
        f"No GitHub credential for {repo}. Set GITHUB_TOKEN, or run `gh auth login` "
        "so `gh auth token` returns one."
    )


def authenticated_remote(repo: str, host: str = "github.com") -> str:
    """An HTTPS remote that carries the credential.

    Keep the result out of logs, error messages and Weave inputs: it contains
    the token in the userinfo field.
    """
    token = get_github_token(repo)
    return f"https://x-access-token:{token}@{host}/{repo}.git"


def redact(text: str, repo: str) -> str:
    """Blank the token out of anything about to be shown or recorded.

    git and gh both echo the remote URL in their output, so run stderr through
    this before it reaches a log, the dashboard, or a trace.
    """
    try:
        token = get_github_token(repo)
    except TokenError:
        return text
    return text.replace(token, "***") if token else text
