import type { RunStatus } from '../api'

const STATUS: Record<RunStatus, { label: string; tone: string; pulse?: boolean }> = {
  running: { label: 'Running', tone: 'info', pulse: true },
  awaiting_approval: { label: 'Awaiting approval', tone: 'warning' },
  completed: { label: 'Completed', tone: 'success' },
  escalated: { label: 'Escalated', tone: 'danger' },
  failed: { label: 'Failed', tone: 'danger' },
}

export function StatusBadge({ status }: { status: RunStatus }) {
  const s = STATUS[status]
  return (
    <span className={`badge ${s.tone}`}>
      <span className={`dot ${s.pulse ? 'pulse' : ''}`} />
      {s.label}
    </span>
  )
}

export function Badge({ tone = 'neutral', children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}
