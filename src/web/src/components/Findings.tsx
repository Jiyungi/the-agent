// Findings, and the same page put through axe-core beside them.
//
// The empty side of the axe column is the point: these five criteria need a
// keyboard, and a parser does not have one. It is read from the WCAG tags on the
// rules axe actually fired here, never asserted from a table.

import type { Finding, Job } from '../api'
import { Icon } from '../Icon'

const NAMES: Record<string, string> = {
  '2.1.1': 'Keyboard',
  '2.1.2': 'No Keyboard Trap',
  '2.4.3': 'Focus Order',
  '2.4.7': 'Focus Visible',
  '2.4.11': 'Focus Not Obscured',
}
const AXE_TAG: Record<string, string> = {
  '2.1.1': 'wcag211', '2.1.2': 'wcag212', '2.4.3': 'wcag243',
  '2.4.7': 'wcag247', '2.4.11': 'wcag2411',
}

interface Props {
  findings: Finding[]
  axe: Job['axe']
}

export function Findings({ findings, axe }: Props) {
  const criteria = Object.keys(NAMES)
  const byTag = new Map<string, string[]>()
  for (const v of axe.violations ?? []) {
    for (const t of v.tags ?? []) {
      byTag.set(t, [...(byTag.get(t) ?? []), v.id])
    }
  }

  let gaps = 0
  const rows = criteria.map((crit) => {
    const mine = findings.filter((f) => f.criterion === crit)
    const failed = mine.filter((f) => f.status === 'failed')
    const ne = mine.filter((f) => f.status === 'not_evaluated')
    const axeHits = [...new Set(byTag.get(AXE_TAG[crit]) ?? [])]
    if (failed.length && !axeHits.length) gaps += 1
    return { crit, mine, failed, ne, axeHits }
  })


  return (
    <>
      {axe.ran && gaps > 0 && (
        <div className="status-label status-neutral" style={{ marginBottom: 14 }}>
          <Icon name="check" />
          <span>
            <b>{gaps} of the five</b> found a defect on this page that axe-core{' '}
            {axe.version} did not report. It ran, and it has no rule that fires on{' '}
            {gaps > 1 ? 'any of them' : 'it'}.
          </span>
        </div>
      )}

      <table className="grid">
        <caption className="sr-only">Findings per criterion, with axe-core beside them</caption>
        <thead>
          <tr>
            <th>Criterion</th>
            <th>ACCEL</th>
            <th>axe-core {axe.version}</th>
            <th className="num">Examined</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ crit, mine, failed, ne, axeHits }) => {
            const examined = mine.reduce((n, f) => n + (f.census?.examined ?? 0), 0)
            return (
              <tr key={crit}>
                <td>
                  <span className="mono">{crit}</span>
                  <span className="sel" style={{ display: 'block', fontSize: '0.72rem' }}>
                    {NAMES[crit]}
                  </span>
                </td>
                <td>
                  {failed.length ? (
                    <>
                      <span className="status-label status-blocked">failed</span>
                      <span className="muted">{failed[0].summary}</span>
                      {failed[0].targets.length > 0 && (
                        <span className="tag-list">
                          {failed[0].targets.slice(0, 4).map((t) => (
                            <code key={t}>{t}</code>
                          ))}
                        </span>
                      )}
                    </>
                  ) : ne.length ? (
                    <>
                      <span className="status-label status-queued">not evaluated</span>
                      <span className="muted">{ne[0].reason}</span>
                    </>
                  ) : mine.length ? (
                    <span className="status-label status-done"><Icon name="check" /> passed</span>
                  ) : (
                    <span className="muted">not run</span>
                  )}
                </td>
                <td>
                  {axeHits.length ? (
                    axeHits.map((h) => <code key={h} className="mono">{h}</code>)
                  ) : (
                    <span className="muted">no rule fired</span>
                  )}
                </td>
                <td className="num">{examined || <span className="muted">—</span>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

    </>
  )
}
