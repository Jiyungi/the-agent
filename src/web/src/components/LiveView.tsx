// The frame and the stop list. Selecting a stop shows that screenshot and draws
// the focused element's own rectangle on it, from the viewport coordinates the
// recorder captured -- hatched red when something was painted over it.
//
// The state tabs take arrow keys with a roving tabindex. A product that reports
// on keyboard access does not ship a tablist you can only use with a mouse.

import { useEffect, useRef, useState } from 'react'
import type { Recording, Stop } from '../api'

const VW = 1024
const VH = 740

interface Props {
  recordings: Record<string, Recording>
}

export function LiveView({ recordings }: Props) {
  const states = Object.keys(recordings)
  const [active, setActive] = useState(states[0] ?? '')
  const [stopIndex, setStopIndex] = useState(0)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])

  useEffect(() => {
    if (!states.includes(active) && states.length) setActive(states[0])
  }, [states, active])

  const rec = recordings[active]

  function onTabKey(e: React.KeyboardEvent, i: number) {
    const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!d) return
    e.preventDefault()
    const next = (i + d + states.length) % states.length
    setActive(states[next])
    setStopIndex(0)
    tabRefs.current[next]?.focus()
  }

  if (!rec) return <p className="muted">No recording yet.</p>

  const stops = rec.stops ?? []
  const withShot = stops.filter((s) => s.screenshot)
  const current: Stop | undefined =
    stops.find((s) => s.index === stopIndex) ?? withShot[0] ?? stops[0]
  const shot = current?.screenshot
    ?? withShot[0]?.screenshot
    ?? null
  const box = current && current.w > 0 && current.h > 0

  return (
    <>
      <div className="tabrow" role="tablist" aria-label="Page states">
        {states.map((s, i) => (
          <button
            key={s}
            ref={(el) => { tabRefs.current[i] = el }}
            role="tab"
            id={`tab-${s}`}
            aria-selected={s === active}
            aria-controls={`panel-${s}`}
            tabIndex={s === active ? 0 : -1}
            onClick={() => { setActive(s); setStopIndex(0) }}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {s}
            {!recordings[s].state_reached && ' · not reached'}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
        {!rec.state_reached ? (
          <div className="quiet-panel">
            <strong>This state was never reached.</strong>
            {rec.reach_note}
          </div>
        ) : (
          <>
            <div className="live">
              <div className="frame">
                {shot ? (
                  <>
                    <img src={`/shot/${shot}`} alt={`The page with focus on ${current?.name ?? current?.selector ?? 'a control'}`} />
                    {box && current && (
                      <div
                        className={`box${current.obscured_by ? ' covered' : ''}`}
                        style={{
                          left: `${((current.vx ?? current.x) / VW) * 100}%`,
                          top: `${((current.vy ?? current.y) / VH) * 100}%`,
                          width: `${(current.w / VW) * 100}%`,
                          height: `${(current.h / VH) * 100}%`,
                        }}
                      />
                    )}
                  </>
                ) : (
                  <div className="frame-empty">
                    No screenshot was captured for this state.
                  </div>
                )}
              </div>

              <div className="stoplist">
                <ol>
                  {stops.map((s) => {
                    const invisible = s.focus_delta !== null && s.focus_delta < 0.005
                    return (
                      <li key={s.index}>
                        <button
                          className="stop-btn"
                          type="button"
                          aria-current={s.index === (current?.index ?? -1)}
                          onClick={() => setStopIndex(s.index)}
                        >
                          <span className="i">{s.index}</span>
                          <span className="nm">
                            {s.name ?? <span className="muted">no accessible name</span>}
                            <span className="sel">
                              {(s.role ?? s.tag.toLowerCase())} · {s.selector}
                            </span>
                          </span>
                          {s.obscured_by ? (
                            <span className="fl">covered</span>
                          ) : invisible ? (
                            <span className="fl">no focus ring</span>
                          ) : s.focus_delta !== null ? (
                            <span className="dl">{s.focus_delta.toFixed(2)}</span>
                          ) : null}
                        </button>
                      </li>
                    )
                  })}
                </ol>
              </div>
            </div>

          </>
        )}
      </div>
    </>
  )
}
