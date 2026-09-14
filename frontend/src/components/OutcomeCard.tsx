import type { Run } from '../api'
import { Badge } from './Badge'

export function OutcomeCard({ run }: { run: Run }) {
  if (run.actions.length === 0 && run.validations.length === 0) return null
  return (
    <div className="card">
      <h3>Execution and validation</h3>
      {run.actions.length > 0 && (
        <div className="list small">
          {run.actions.map((a, i) => (
            <div key={i} className="list-row">
              <span className="mono">{a.kind}</span>
              <span>
                {String(a.request.quantity ?? '')} {a.request.supplier_id ? `from ${a.request.supplier_id}` : ''} {a.request.po_id ? `on ${a.request.po_id}` : ''}
                {a.result.reason && <span className="muted"> — {a.result.reason}</span>}
              </span>
              <span>
                {a.result.ok ? (
                  <Badge tone="success">{String(a.result.status ?? 'ok')} {a.result.po_id ? `· ${a.result.po_id}` : ''}</Badge>
                ) : (
                  <Badge tone="danger">rejected</Badge>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
      {run.validations.map((v, i) => (
        <div key={i} className={`banner ${v.ok ? 'success' : 'danger'}`}>
          <div className="row">
            <strong>Validation {run.validations.length > 1 ? `#${i + 1}` : ''}</strong>
            <Badge tone={v.ok ? 'success' : 'danger'}>{v.ok ? 'matches expectation' : `${v.mismatches.length} mismatch${v.mismatches.length === 1 ? '' : 'es'}`}</Badge>
          </div>
          {v.mismatches.length > 0 && (
            <ul className="plain small">
              {v.mismatches.map((m, j) => (
                <li key={j}>{m}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>expected vs actual</summary>
            <pre>{JSON.stringify({ expected: v.expected, actual: v.actual }, null, 2)}</pre>
          </details>
        </div>
      ))}
    </div>
  )
}
