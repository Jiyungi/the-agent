// The browsers, live.
//
// Each page gets its own sandbox, its own Chromium and its own desktop. This
// shows every one of them at once, so a multipage audit is something you watch
// rather than something you wait for.

import { useState } from 'react'
import { Icon } from '../Icon'
import type { Job } from '../api'

function statusFor(status: string): string {
  return status === 'done' ? 'status-done'
    : status === 'failed' ? 'status-blocked'
    : status === 'skipped' ? 'status-attention'
    : status === 'running' ? 'status-live'
    : 'status-queued'
}

export function WatchPanel({ job, live }: { job: Job; live: boolean }) {
  const [open, setOpen] = useState(live)

  // Lane 0 IS the entry page, with its own stream. Prepending job.watch_url as
  // well showed the same sandbox twice -- "bit-estate.vercel.app/" beside
  // "bit-estate.vercel.app" -- and turned four lanes into five panels.
  //
  // A lane whose desktop has not come up yet has no watch_url, and it stays in
  // the grid as a placeholder rather than being filtered out: dropping it would
  // report three browsers for four running sandboxes.
  const lanes = job.lanes ?? []
  const screens = lanes.length > 0
    ? lanes
    : (job.watch_url
        ? [{ index: 0, url: job.url, label: '', watch_url: job.watch_url,
             status: job.status === 'running' ? 'running' : job.status,
             note: '', failed: 0, checks: 0, sandbox_id: '', source: '',
             duplicate_of: '' }]
        : [])
  if (screens.length === 0) return null

  const wide = screens.length === 1

  // What a blank desktop means right now, or '' when it should be showing
  // something and a blank one is a real problem.
  const idle =
    job.phase === 'clone' ? 'Nothing opened yet — reading the repository'
    : job.phase === 'fix' ? 'Paused while the project rebuilds'
    : job.phase === 'pr' ? 'Audit finished — this sandbox is being released'
    : ''

  return (
    <section className="section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Live</span>
          <h2>{screens.length === 1 ? 'The browser' : `${screens.length} browsers`}</h2>
          <p>One sandbox per page. Each desktop below is a real Chromium being
             driven, streamed as it happens.</p>
        </div>
        <div className="page-action-row">
          <button className="button secondary" onClick={() => setOpen(!open)}>
            <Icon name={open ? 'close' : 'eye'} />
            {open ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>

      {open && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: wide ? 'minmax(0, 1fr)' : 'repeat(auto-fit, minmax(340px, 1fr))',
          gap: 14,
        }}>
          {screens.map((s) => (
            <figure key={s.index} style={{ margin: 0, minWidth: 0 }}>
              <figcaption style={{ display: 'flex', alignItems: 'center', gap: 8,
                                   marginBottom: 8, flexWrap: 'wrap' }}>
                <span className={`status-label ${s.duplicate_of
                  ? 'status-attention' : statusFor(s.status)}`}>
                  <i aria-hidden="true" />{s.duplicate_of ? 'same screen' : s.status}
                </span>
                <span className="mono" style={{ fontSize: 'var(--text-caption)',
                        overflow: 'hidden', textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap', minWidth: 0 }}>
                  {s.label || s.url.replace(/^https?:\/\//, '')}
                </span>
                <a className="button secondary" href={s.watch_url}
                   target="_blank" rel="noreferrer"
                   style={{ marginLeft: 'auto', minHeight: 30, padding: '0 10px' }}>
                  <Icon name="external" />
                </a>
              </figcaption>
              <div className="frame" style={{ position: 'relative' }}>
                {/* A live desktop with nothing on it looks identical to a
                    broken panel. Two phases produce one on purpose: during the
                    clone nothing has been opened yet, and during the fix the
                    browser and desktop are stopped to free memory for the
                    build. Both are normal and neither used to say so. */}
                {s.watch_url && idle && (
                  <p className="frame-note">{idle}</p>
                )}
                {s.watch_url ? (
                  <iframe
                    key={s.watch_url}
                    src={s.watch_url}
                    title={`The browser auditing ${s.url}`}
                    style={{ width: '100%', height: '100%', border: 0, display: 'block' }}
                  />
                ) : (
                  <div className="frame-empty">
                    {s.status === 'done' || s.status === 'failed'
                      ? 'This sandbox had no live view'
                      : 'Starting the desktop'}
                  </div>
                )}
              </div>
              {s.note && (
                <p className="muted" style={{ fontSize: 'var(--text-caption)', marginTop: 6 }}>
                  {s.note}
                </p>
              )}
            </figure>
          ))}
        </div>
      )}
    </section>
  )
}
