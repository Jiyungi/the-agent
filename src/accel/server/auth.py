"""Sign in with GitHub, through Supabase.

The flow, in the order it happens:

  1. The browser calls `supabase.auth.signInWithOAuth` with the `repo` scope
     and goes to GitHub.
  2. GitHub sends the person back to Supabase, which sends them back here.
  3. The browser posts the session to `/api/session`, and this file stores it.

**Step 3 is not optional, and it is the part that is easy to get wrong.**
Supabase hands back the GitHub token exactly once, as `provider_token`, in the
session object the browser receives. It does not store it and there is no
endpoint to ask for it later. If it is not captured at that moment, you are
left with a signed-in user whose repositories you cannot touch -- everything
looks fine until the first clone, which fails with a 404 that reads like the
repository does not exist.

So `provider_token` is written to the `users` row on sign-in, and read from
there for every clone and every pull request afterwards.

The access token Supabase issues for the person themselves is a JWT signed by
the project. `identify` verifies it against Supabase rather than trusting it,
because a token that merely decodes is not a token that is real.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import db


@dataclass
class User:
    id: str
    github_login: str
    github_token: str
    avatar_url: str = ""

    @property
    def can_touch_repos(self) -> bool:
        """A signed-in user without a GitHub token can read, not act."""
        return bool(self.github_token)


def _supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    return url


def _publishable_key() -> str:
    return os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()


def verify(access_token: str) -> dict | None:
    """Ask Supabase who this token belongs to. None if it is not valid.

    Never decode the JWT locally and trust the claims: anyone can mint a
    well-formed JWT. Supabase is the only thing that knows whether this one was
    signed by the project and has not been revoked.
    """
    if not access_token:
        return None
    req = urllib.request.Request(
        f"{_supabase_url()}/auth/v1/user",
        headers={"Authorization": f"Bearer {access_token}",
                 "apikey": _publishable_key()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def sign_in(access_token: str, provider_token: str) -> User | None:
    """Record the sign-in. `provider_token` is the GitHub token, seen once.

    An existing user who signs in again without a provider token -- a page
    refresh, a restored session -- keeps the token already stored. Overwriting
    it with an empty string would silently disable their next run.
    """
    who = verify(access_token)
    if not who:
        return None

    meta = who.get("user_metadata") or {}
    login = (meta.get("user_name") or meta.get("preferred_username")
             or meta.get("nickname") or who.get("email", "").split("@")[0] or "unknown")
    avatar = meta.get("avatar_url", "")

    db.execute("""
        INSERT INTO users (id, github_login, github_token, avatar_url, last_seen_at)
        VALUES (:id, :login, NULLIF(:tok, ''), :avatar, now())
        ON CONFLICT (id) DO UPDATE SET
            github_login = EXCLUDED.github_login,
            avatar_url   = EXCLUDED.avatar_url,
            last_seen_at = now(),
            github_token = COALESCE(NULLIF(EXCLUDED.github_token, ''),
                                    users.github_token)
    """, {"id": who["id"], "login": login, "tok": provider_token or "",
          "avatar": avatar})

    return current(access_token)


def current(access_token: str) -> User | None:
    """The signed-in user, with their stored GitHub token, or None."""
    who = verify(access_token)
    if not who:
        return None
    row = db.query_one("SELECT * FROM users WHERE id = :id", {"id": who["id"]})
    if not row:
        return None
    return User(id=row["id"], github_login=row["github_login"],
                github_token=row.get("github_token") or "",
                avatar_url=row.get("avatar_url") or "")


def bearer(headers) -> str:
    """The token out of an Authorization header, or empty."""
    raw = ""
    try:
        raw = headers.get("Authorization", "") or headers.get("authorization", "")
    except Exception:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def config_for_browser() -> dict:
    """What the frontend needs to start a sign-in. Publishable values only."""
    return {"supabase_url": _supabase_url(),
            "supabase_key": _publishable_key(),
            "configured": bool(_supabase_url() and _publishable_key())}
