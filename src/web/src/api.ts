// One place that knows the server exists. Same-origin paths: Vite proxies them
// in development, the Python server serves the build in production.

export interface Stop {
  index: number
  tag: string
  role: string | null
  name: string | null
  x: number; y: number; w: number; h: number
  vx?: number; vy?: number
  selector: string
  parent?: string | null
  screenshot: string | null
  obscured_by: string | null
  focus_delta: number | null
}

export interface Recording {
  url: string
  state: string
  state_reached: boolean
  reach_note: string
  truncated: boolean
  stops: Stop[]
  candidates: unknown[]
  excluded: { rule: string; reason: string; selector: string; tag: string }[]
  focusable_total?: number
  consent_note?: string
  nav_error?: string
  http_status?: number
  title?: string
}

export interface Census {
  examined: number; failed: number; passed: number
  undecided: number; inapplicable: number
  excluded: number; exclusion_note: string
}

export interface Finding {
  criterion: string
  state: string
  status: 'passed' | 'failed' | 'not_evaluated'
  reason: string | null
  summary: string
  targets: string[]
  evidence_refs: string[]
  census?: Census
}

export interface Patch {
  criterion: string
  component: string
  status: string
  patch_attempts: number
  locate_attempts: number
  closed: number
  created: number
  reason: string
  lesson_ids: number[]
}

export interface JobEvent { at: number; phase: string; message: string; level: string }

export interface Job {
  id: string
  url: string
  repo: string
  status: 'queued' | 'running' | 'done' | 'failed'
  phase: string
  source: string
  events: JobEvent[]
  findings: Finding[]
  recordings: Record<string, Recording>
  axe: { ran: boolean; version: string; violations: AxeViolation[] }
  patches: Patch[]
  lesson_ids: number[]
  watch_url: string
  pages: { url: string; source: string; findings: number }[]
  lanes: { index: number; url: string; label: string
           sandbox_id: string; watch_url: string
           status: string; note: string; source: string
           failed: number; checks: number; duplicate_of: string }[]
  pr_url: string
  pr_blocked: string
  diff: string
  trace_url: string
  error: string
  started: number
  finished: number
  elapsed: number
}

export interface AxeViolation {
  id: string; impact: string; help: string
  tags: string[]; nodes: number; targets: string[]
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const text = await res.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { /* not JSON */ }
  if (!res.ok) {
    const msg = (body as { error?: string } | null)?.error
    throw new Error(msg ?? `The server returned ${res.status}.`)
  }
  return body as T
}

export const api = {
  startAudit: (url: string, repo: string, states: string[], fix: boolean, pr: boolean) =>
    req<Job>('/api/audit', {
      method: 'POST',
      body: JSON.stringify({ url, repo, states, fix, pr }),
    }),
  job: (id: string) => req<Job>(`/api/job/${id}`),
  jobs: () => req<{ jobs: { id: string; url: string; repo: string; status: string
                           phase: string; findings: number; pr_url: string
                           pages: number; watch_url: string
                           started: number; finished: number }[] }>('/api/job'),
  audits: () => req<{ runs: { run_id: string; url: string; states: number
                             stops: number; frames: number
                             counts: Record<string, number> }[] }>('/api/audits'),
  audit: (id: string) => req<{ summary: Record<string, unknown>; findings: Finding[]
                               recordings: Record<string, Recording>
                               axe: Job['axe'] }>(`/api/audit/${id}`),
  benchmark: () => req<BenchmarkPayload>('/api/benchmark'),
  loop: () => req<LoopPayload>('/api/loop'),
}

export interface BenchmarkRow {
  criterion: string; run: boolean
  planted: number; found: number; reported: number
  true_positives: number; false_positives: number; not_evaluated: number
  axe_wcag: number; axe_best_practice: number; axe_ran: boolean
  axe_items: { id: string; help: string; nodes: number; wcag: boolean }[]
}

export interface BenchmarkPayload {
  fixture: {
    tag: string; rows: BenchmarkRow[]
    planted: number; found: number; not_evaluated: number
    true_positives: number; false_positives: number; reported: number
    axe_wcag: number; axe_best_practice: number; changed: string
  }[]
  unknown: {
    key: string; label: string; url: string
    rows: { criterion: string; examined: number; failed: number; passed: number
            undecided: number; inapplicable: number; excluded: number
            statuses: string[]; targets: string[] }[]
    targets: string[]; checked: number; correct: number; changed: string
  }[]
  weave: string
}

export interface LoopArm {
  tag: string; arm: string; lessons: string
  closed: { order: number; criterion: string; component: string; page: string
            patch_attempts: number; lessons_retrieved: number
            lesson_ids: number[]; call_id: string; trace: string }[]
  n_closed: number; created: number
  mean_attempts: number | null; one_attempt: number
  trend: number | null; retrieved_total: number; changed: string
}

export interface LoopPayload {
  control: LoopArm | null
  treatment: LoopArm | null
  total_closed: number
  total_one_attempt: number
}
