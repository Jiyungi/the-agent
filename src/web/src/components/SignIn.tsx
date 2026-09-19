// The gate. Nothing else is reachable without a GitHub token, because every
// run clones a repository and opens a pull request as the person signing in.
//
// One screen, one claim, one button. The earlier version listed all five
// criteria with a sentence each, which is a specification, not a landing page
// -- nobody reads five blurbs before deciding whether to sign in.
//
// What replaces it is the coverage line. `5 / 55` says where ACCEL is against
// WCAG 2.2 Level AA without a word of hedging, and it reframes the five from a
// limit into progress. The number is the whole argument, so it gets the space
// a paragraph would have taken.

import { BrandMark } from '../Icon'
import { signIn } from '../auth'

/** Success criteria live today, against WCAG 2.2 Level AA (A + AA = 55). */
const LIVE = 5
const TARGET = 55

export function SignIn() {
  const pct = Math.round((LIVE / TARGET) * 100)

  return (
    <main className="gate" id="main">
      <div className="gate-inner">
        <span className="gate-brand">
          <BrandMark />
          <span>ACCEL</span>
        </span>

        <h1 className="gate-title">
          Accessibility that<br />proves itself.
        </h1>

        <p className="gate-lede">
          ACCEL uses your site in a real browser the way assistive technology
          does, finds what a scanner cannot see, and opens a pull request with
          the fix.
        </p>

        <button type="button" className="gate-cta" onClick={() => void signIn()}>
          <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true" fill="currentColor">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
              0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01
              1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95
              0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0
              1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0
              3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0
              .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
          </svg>
          Sign in with GitHub
        </button>

        <div className="gate-coverage">
          <div className="gate-coverage-top">
            <span className="gate-count">
              <strong>{LIVE}</strong> of {TARGET}
            </span>
            <span className="gate-standard">WCAG 2.2 Level AA</span>
          </div>
          <div
            className="gate-bar"
            role="progressbar"
            aria-valuenow={LIVE}
            aria-valuemin={0}
            aria-valuemax={TARGET}
            aria-label={`${LIVE} of ${TARGET} WCAG 2.2 Level AA success criteria`}
          >
            <span style={{ width: `${pct}%` }} />
          </div>
        </div>
      </div>
    </main>
  )
}
