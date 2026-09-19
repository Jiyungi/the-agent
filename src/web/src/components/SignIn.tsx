// The gate. Nothing else is reachable without a GitHub token, because every
// run clones a repository and opens a pull request as the person signing in.
//
// The hero is the product, running. A miniature interface sits beside the
// claim and a focus ring walks it, one stop at a time, exactly as ACCEL walks
// a real page -- and then reaches a control the keyboard cannot get to, which
// is the moment the whole thing exists for. A sentence saying "finds what a
// scanner cannot see" asks to be believed. Watching the ring skip a button
// and the button turn red does not.
//
// The ring is CSS keyframes over a fixed list of positions, not a simulation.
// Nothing here talks to the server and nothing can fail on stage.
//
// Nothing on this screen names a criterion, a count, or a WCAG version. What
// ACCEL checks this week is an implementation detail; putting it on the door
// makes the scope the product.

import { useEffect, useState } from 'react'
import { BrandMark } from '../Icon'
import { signIn } from '../auth'

/** Where the ring stops, in order, as percentages of the mock frame. */
const STOPS = [
  { top: 13, left: 6, w: 22, h: 9 },     // nav item
  { top: 13, left: 31, w: 18, h: 9 },    // nav item
  { top: 38, left: 6, w: 40, h: 12 },    // the input
  { top: 38, left: 50, w: 20, h: 12 },   // the button beside it
  { top: 68, left: 6, w: 26, h: 11 },    // footer link
]

/** The control the keyboard never reaches. Not in STOPS, on purpose. */
const MISSED = { top: 68, left: 62, w: 30, h: 11 }

export function SignIn() {
  const [i, setI] = useState(0)
  const [caught, setCaught] = useState(false)

  // One pass over the stops, hold on the miss, then start again. `caught` has
  // to be cleared on the way round: leaving it set meant the red control and
  // the red caption were simply always there, which is a still image, not a
  // demonstration.
  useEffect(() => {
    const t = setInterval(() => {
      setI((n) => {
        if (n < STOPS.length - 1) return n + 1
        setCaught(true)
        window.setTimeout(() => setCaught(false), 2600)
        return 0
      })
    }, 850)
    return () => clearInterval(t)
  }, [])

  const at = STOPS[i]

  return (
    <main className="gate" id="main">
      <div className="gate-grid">
        <div className="gate-copy">
          <span className="gate-brand">
            <BrandMark />
            <span>ACCEL</span>
          </span>

          <h1 className="gate-title">
            Ship fast.<br />Stay accessible.
          </h1>

          <p className="gate-lede">
            Give ACCEL your site and your GitHub repo. Get back a pull request
            that fixes your accessibility bugs.
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
        </div>

        {/* Decorative: it repeats what the copy already says. */}
        <div className="gate-stage" aria-hidden="true">
          <div className="mock">
            <div className="mock-chrome">
              <span /><span /><span />
            </div>

            <div className="mock-body">
              <div className="mock-el" style={{ top: '13%', left: '6%', width: '22%', height: '9%' }} />
              <div className="mock-el" style={{ top: '13%', left: '31%', width: '18%', height: '9%' }} />
              <div className="mock-el mock-title" style={{ top: '25%', left: '6%', width: '52%', height: '6%' }} />
              <div className="mock-el" style={{ top: '38%', left: '6%', width: '40%', height: '12%' }} />
              <div className="mock-el mock-solid" style={{ top: '38%', left: '50%', width: '20%', height: '12%' }} />
              <div className="mock-el mock-line" style={{ top: '55%', left: '6%', width: '64%', height: '4%' }} />
              <div className="mock-el" style={{ top: '68%', left: '6%', width: '26%', height: '11%' }} />
              <div
                className={`mock-el mock-missed${caught ? ' is-caught' : ''}`}
                style={{
                  top: `${MISSED.top}%`, left: `${MISSED.left}%`,
                  width: `${MISSED.w}%`, height: `${MISSED.h}%`,
                }}
              />

              <div
                className="ring"
                style={{
                  top: `${at.top}%`, left: `${at.left}%`,
                  width: `${at.w}%`, height: `${at.h}%`,
                }}
              />
            </div>
          </div>

          <p className={`stage-caption${caught ? ' is-caught' : ''}`}>
            {caught
              ? 'Tab never reaches it. A scanner sees valid HTML.'
              : 'Walking the page, one Tab at a time…'}
          </p>
        </div>
      </div>
    </main>
  )
}
