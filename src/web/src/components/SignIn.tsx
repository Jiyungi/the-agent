// The gate. Nothing else in the product is reachable without a GitHub token,
// because every run clones a repository and opens a pull request as the person
// who started it.
//
// The criteria listed here are what ACCEL checks TODAY, not what ACCEL is.
// This screen has to say that plainly, because a list of five reads as a
// limit unless it is labelled as a front. The product is an accessibility
// agent; the coverage grows, and the copy must not have to be rewritten each
// time it does.

import { BrandMark } from '../Icon'
import { signIn } from '../auth'

const LIVE_CHECKS = [
  { id: '2.1.1', name: 'Keyboard', blurb: 'A control you can click but never reach by Tab' },
  { id: '2.1.2', name: 'No Keyboard Trap', blurb: 'Focus goes in and cannot get out' },
  { id: '2.4.3', name: 'Focus Order', blurb: 'Tab jumps backwards up the page' },
  { id: '2.4.7', name: 'Focus Visible', blurb: 'A focus ring that exists in CSS and not on screen' },
  { id: '2.4.11', name: 'Focus Not Obscured', blurb: 'Focus lands behind a sticky header' },
]

export function SignIn() {
  return (
    <main className="signin-shell" id="main">
      <div className="signin-card">
        <span className="brand signin-brand">
          <BrandMark />
          <span>ACCEL</span>
        </span>

        <h1>Accessibility that proves itself.</h1>
        <p className="signin-lede">
          ACCEL opens your deployed site in a real browser and uses it the way
          someone relying on assistive technology would. It records what
          actually happens, finds the component behind each failure, writes the
          fix, and opens a pull request on your repository — and it only calls
          a finding closed when the same check stops firing on the rebuilt page.
        </p>

        <div className="signin-section-head">
          <h2>Checking now</h2>
          <span>WCAG 2.2 · more criteria landing continuously</span>
        </div>

        <ul className="signin-criteria">
          {LIVE_CHECKS.map((c) => (
            <li key={c.id}>
              <code>{c.id}</code>
              <strong>{c.name}</strong>
              <span>{c.blurb}</span>
            </li>
          ))}
        </ul>

        <p className="signin-note">
          Most of these have no axe-core rule at all. A scanner reading your
          HTML cannot find them — you have to open the page and use it.
        </p>

        <button type="button" className="signin-button" onClick={() => void signIn()}>
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

        <p className="signin-scope">
          ACCEL asks for repository access so it can read your source and open a
          pull request. It only ever touches the repository you name, and it
          opens pull requests — it never pushes to your default branch.
        </p>
      </div>
    </main>
  )
}
