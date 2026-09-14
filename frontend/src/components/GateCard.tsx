import { Fragment, useState } from 'react'
import type { Run } from '../api'
import { Badge } from './Badge'

export function GateCard({ run, onApprove, onReject, busy }: { run: Run; onApprove: () => void; onReject: (note: string) => void; busy: boolean }) {
  const [note, setNote] = useState('')
  const g = run.gate
  if (!g) return null
  const awaiting = run.status === 'awaiting_approval'
  return (
    <div className="card">
      <div className="card-header">
        <h3>Policy gate</h3>
        {g.hard_fail ? <Badge tone="danger">blocked</Badge> : g.requires_approval ? <Badge tone="warning">needs approval</Badge> : <Badge tone="success">passed</Badge>}
      </div>
      {g.reports.map((r, i) => (
        <div key={i} className="stack">
          <div className="row small muted">
            <span>
              {r.proposed_qty} units from <span className="mono">{r.supplier_id}</span>
            </span>
            <span>· value {r.value}</span>
            <span>· max feasible {r.max_feasible_qty}</span>
            {r.deviation_from_recommendation_pct !== null && <span>· deviation {r.deviation_from_recommendation_pct}%</span>}
          </div>
          <div className="checks small">
            {r.checks.map((c) => (
              <Fragment key={c.name}>
                <span className={`check-${c.status} mono`}>{c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✕'}</span>
                <span className="mono">{c.name}</span>
                <span className={c.status === 'ok' ? 'muted' : ''}>{c.detail}</span>
              </Fragment>
            ))}
          </div>
        </div>
      ))}
      {awaiting && (
        <div className="banner warning">
          <strong>Human approval required</strong>
          <ul className="plain small">
            {g.approval_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
          <textarea rows={2} placeholder="Optional note for the audit log" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={onApprove}>
              Approve and execute
            </button>
            <button className="btn danger" disabled={busy} onClick={() => onReject(note)}>
              Reject
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
