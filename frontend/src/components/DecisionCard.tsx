import type { Decision, Run } from '../api'
import { Badge } from './Badge'

const TONE: Record<Decision['action'], string> = {
  accept: 'success',
  modify: 'info',
  reject: 'neutral',
  investigate: 'warning',
  escalate: 'danger',
}

export function DecisionCard({ run }: { run: Run }) {
  const d = run.decision
  if (!d) {
    return (
      <div className="card">
        <h3>Decision</h3>
        <p className="muted">The agent is still investigating.</p>
      </div>
    )
  }
  return (
    <div className="card">
      <div className="card-header">
        <h3>Decision</h3>
        <div className="row">
          {run.recovery_turns_used > 0 && <Badge tone="warning">revised ×{run.recovery_turns_used}</Badge>}
          <Badge tone="neutral">confidence {(d.confidence * 100).toFixed(0)}%</Badge>
          <Badge tone={TONE[d.action]}>{d.action.toUpperCase()}</Badge>
        </div>
      </div>
      <p>{d.rationale}</p>
      <dl className="kv small">
        {d.quantity !== null && (
          <>
            <dt>Quantity</dt>
            <dd>{d.quantity} units</dd>
          </>
        )}
        {d.supplier_id && (
          <>
            <dt>Supplier</dt>
            <dd className="mono">{d.supplier_id}</dd>
          </>
        )}
        {d.po_id && (
          <>
            <dt>Existing PO</dt>
            <dd className="mono">{d.po_id}</dd>
          </>
        )}
        {d.additional_orders.length > 0 && (
          <>
            <dt>Additional orders</dt>
            <dd>{d.additional_orders.map((o) => `${o.quantity} from ${o.supplier_id}`).join(', ')}</dd>
          </>
        )}
        <dt>Expected outcome</dt>
        <dd className="mono">{JSON.stringify(d.expected_outcome)}</dd>
      </dl>
      {d.key_factors.length > 0 && (
        <div className="stack">
          <h3>Key factors</h3>
          <ul className="plain small">
            {d.key_factors.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
