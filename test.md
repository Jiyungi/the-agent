# Tests to run on the finished product

## 1. The child-component test — does the CSS-class fix actually work?

**Run this against:** `https://bit-estate.vercel.app/verify`
with repo `https://github.com/Carldtitan/BitEstate-Capstone-`

**The bug we are testing for.** The failing element is the small question-mark
button, reported as `span>button`. It is NOT written in `VerifyPage.jsx`. That
file only contains:

```jsx
<HelpTooltip>The file stays in your browser...</HelpTooltip>
```

The button itself lives in `src/components/HelpTooltip.jsx`:

```jsx
<button className="help-trigger" type="button" aria-label={label}>
```

**What used to happen.** Ally picked the file by page name (`/verify` →
`VerifyPage.jsx`) and showed the model a file that did not contain the button.
The model invented the lines to search for, the search found nothing, and the
run died with:

```
could not locate the code after 3 attempts on patch attempt 1:
the find text matched 0 times (already fixed, or never there)
```

This happened on **19 of 19 consecutive runs**. Every BitEstate artifact in
`artifacts/job-*.json` from that period shows it.

**The fix being tested.** Pick the file from the element's CSS class instead of
the page name: `grep -rl "help-trigger" src/` → `src/components/HelpTooltip.jsx`.

**Pass condition:**
- The run reports that the source file for this finding is `HelpTooltip.jsx`,
  not `VerifyPage.jsx`
- `locate_attempts` is 1, not 3
- The status is NOT `could_not_locate`
- A patch is actually applied and re-audited

**Fail condition:** any run that still reports `the find text matched 0 times`
on an element that a child component renders.

**Also check the reverse case.** `/verify` also has a finding on an `<a>` element
that genuinely IS in `VerifyPage.jsx`. The new class-based lookup must still send
that one to `VerifyPage.jsx`. If it now sends both findings to the same file, the
lookup is matching too loosely.

---

## 2. The deletion test — does the behaviour check hold?

**Background.** PR #4 on BitEstate closed a finding by deleting the button:

```diff
-  <Link className="btn page-action" to="/source-truth">
-    New source
-    <ArrowRight size={16} aria-hidden="true" />
-  </Link>
```

The re-audit confirmed the finding was gone, because the element was gone.

**Pass condition:** a patch that removes an interactive element from the page is
not silently accepted. Either it is rejected, or it is flagged in the PR body as
"this fix removes X" for a human to approve.

**Must NOT block:** deleting `outline: none` from CSS, or removing a positive
`tabindex` attribute. Those delete code but remove nothing from the page, and
they are correct fixes. If the check blocks these, it is comparing diffs instead
of comparing the page.

---

## 3. Real-site precision — the number that describes the product

15/15 on the planted-defect fixture and 0/41 on ikea.com are both true. The
second one is the one that describes the product.

Re-run a real site after the visibility filter and the roving-tabindex change,
and record the count. Target: fewer than 41 findings with at least one correct.
