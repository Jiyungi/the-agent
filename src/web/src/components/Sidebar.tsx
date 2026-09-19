// The sidebar structure the CSS depends on:
//
//   .sidebar            space-between
//     .brand-row          .brand > BrandMark + name
//     .sidebar-context    <small>Target</small><strong>owner/repo</strong>
//     .side-nav           .nav-item[aria-current="page"] > Icon + <span>
//     .sidebar-bottom     .account-link > .avatar + .account-copy

import { BrandMark, Icon, type IconName } from '../Icon'

export type Tab = 'home' | 'runs' | 'findings'

interface Props {
  tab: Tab
  setTab: (t: Tab) => void
  target: string
  runCount: number
  findingCount: number
  login: string
  avatarUrl: string
  onSignOut: () => void
}

const NAV: { id: Tab; label: string; icon: IconName }[] = [
  { id: 'home', label: 'Home', icon: 'home' },
  { id: 'runs', label: 'Runs', icon: 'activity' },
  { id: 'findings', label: 'Findings', icon: 'warning' },
]

export function Sidebar({ tab, setTab, target, runCount, findingCount,
                         login, avatarUrl, onSignOut }: Props) {
  const badge = (id: Tab) =>
    id === 'runs' ? runCount : id === 'findings' ? findingCount : 0

  return (
    <aside className="sidebar" aria-label="Main">
      <div className="brand-row">
        <span className="brand">
          <BrandMark />
          <span>ACCEL</span>
        </span>
      </div>

      <p className="sidebar-context">
        <small>Target</small>
        <strong>{target || 'None yet'}</strong>
      </p>

      <nav className="side-nav" aria-label="Sections">
        {NAV.map((item) => (
          <button
            key={item.id}
            type="button"
            className="nav-item"
            aria-current={tab === item.id ? 'page' : undefined}
            onClick={() => setTab(item.id)}
            style={{ width: '100%', background: 'none', cursor: 'pointer', textAlign: 'left' }}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
            {badge(item.id) > 0 && (
              <span className="section-count" style={{ marginLeft: 'auto' }}>
                {badge(item.id)}
              </span>
            )}
          </button>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <span className="account-link">
          {avatarUrl
            ? <img className="avatar" src={avatarUrl} alt="" width={32} height={32} />
            : <span className="avatar" aria-hidden="true">
                {(login || '?').slice(0, 2).toUpperCase()}
              </span>}
          <span className="account-copy">
            <strong>{login || 'Signed in'}</strong>
            <button type="button" className="signout" onClick={onSignOut}>Sign out</button>
          </span>
        </span>
      </div>
    </aside>
  )
}
