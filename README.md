# ACCEL

**Ship fast. Stay accessible.**

Give ACCEL your site and your GitHub repo. Get back a pull request that fixes
your accessibility bugs.

**Live:** https://the-agent-5dk9.onrender.com

---

## What it does

ACCEL opens your deployed site in a real browser in the cloud and uses it the
way someone relying on a keyboard or a screen reader would. It records what
actually happens — every place focus lands, what the screen looked like, what
the accessibility tree said — then finds the component in your code that
produced each failure, writes the fix, rebuilds your project, and checks the
same page again.

A finding is closed only when the check that was failing stops firing on the
rebuilt page. Never because the patch applied. Never because the code
compiled.

Then it opens a pull request.

## Why not just run a scanner

Scanners read your HTML. That finds missing alt text and low contrast, which
matters. It cannot find:

- a focus ring that exists in your CSS and is invisible on screen
- a tab order that jumps backwards up the page
- a `<div>` with an `onClick` that the keyboard never reaches
- focus landing behind a sticky header
- a modal that traps focus and will not let go

None of those are visible in the markup. You have to open the page and press
Tab. ACCEL runs axe-core alongside its own checks on the same page, and reports
where axe fired and where it found nothing — so the comparison is measured on
your site, not asserted here.

## What it will not do

- **Claim a fix it cannot prove.** Three outcomes, not one: the fix was
  verified, a patch was written but the re-audit did not confirm it, or nothing
  could be patched and here is the evidence. The pull request title says which.
- **Call a page clean that it barely saw.** If the tab sequence reached only a
  fraction of the page, a pass would not mean anything, so it reports
  `not evaluated` with the numbers rather than a green tick.
- **Conclude from a page it never engaged.** If focus never left the document,
  every check says so, instead of one of them confidently reporting a keyboard
  trap that is not there.
- **Push to your default branch.** It opens pull requests.

## How it works

```
  your site + your repo
          │
          ├─ reads your router to find the pages that exist
          ├─ opens each one in its own cloud sandbox, in real Chromium
          ├─ presses Tab, records every stop with a screenshot
          ├─ judges five WCAG criteria against that recording
          ├─ finds the component that drew the failing element
          ├─ rewrites it, rebuilds the project, audits the page again
          └─ opens the pull request
```

Four pages are audited at once, each in its own sandbox, and you can watch all
four browsers being driven live.

### Which file to patch

The failing element is usually not written in the page's own file. A button
inside `<HelpTooltip>` lives in `HelpTooltip.jsx`, not in the page that uses
it. ACCEL identifies the file from the element's own CSS class, captured in the
browser at record time, and searches the repository for it. Getting this wrong
is the difference between a patch that applies and one that has nothing to
match against.

### Where AI is used, and where it is not

| Step | Decided by |
|---|---|
| Writing the code fix | **Claude Opus 5**, with extended thinking |
| Whether a focus-order difference actually harms someone | **Claude Sonnet 5** |
| Whether an element is reachable, trapped, visible, obscured | Arithmetic over the recording |
| **Whether a fix worked** | **Re-running the check. Never a model.** |

A verification system that asks a model to grade its own work is not
verification.

## Running it locally

```bash
cd src
pip install -r requirements.txt
python -m accel.server.app        # http://127.0.0.1:8000
```

Working on the frontend:

```bash
cd src/web
npm install
npm run dev                       # proxies /api to the Python server
```

The built frontend is committed, because the deployment host does not have the
memory to build it.

### Environment

```
ANTHROPIC_API_KEY          Opus writes patches, Sonnet judges focus order
DAYTONA_API_KEY            the sandboxes the browsers run in
DAYTONA_API_URL
ACCEL_SNAPSHOT             a 4 CPU / 8 GB sandbox image (default: "ally")
SUPABASE_URL               sign-in
SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY        server only
DATABASE_URL               Postgres, session pooler
```

GitHub sign-in is configured inside Supabase, so no GitHub secret is needed
here. Sign-in requests the `repo` scope, which is what lets ACCEL clone a
repository and open a pull request as you.

## Layout

```
src/accel/agent/     the audit
  session.py         a sandbox with Chromium on a virtual display
  browser.py         drives the page and records every focus stop
  recording.py       the types everything downstream reads
  checks.py          the five criteria
  axe.py             axe-core, run alongside as a control
  judge.py           the one model call in an audit
  whichfile.py       which file draws the element that failed
  patcher.py         asks Opus for an edit
  fixloop.py         patch, rebuild, re-audit, count what closed
  pr.py              branch, commit, push, pull request

src/accel/server/    the product
  app.py             the web server
  auth.py            sign in with GitHub, through Supabase
  db.py              every database call
  jobs.py            one run, start to finish
  fleet.py           four sandboxes at once
  watch.py           relays the live browser view from our own origin

src/web/             the dashboard
src/bench/           15 planted defects and the gate that scores against them
```

## Tests

```bash
cd src
python -m pytest bench/test_recall_gate.py
```

The gate replays frozen browser recordings through all five checks and scores
them against a manifest of planted defects. It fails on any drop in recall, any
new false positive, and any finding on the clean page. Recording a page is the
expensive part; the checks are pure functions of a recording, so the whole
benchmark replays in about a second and can run on every change to a rule.

## Status

A hackathon build. It audits real sites, finds defects scanners do not report,
and opens pull requests. Coverage of WCAG is partial and growing, and the
reporting is written so you can tell what was actually checked from what was
not.
