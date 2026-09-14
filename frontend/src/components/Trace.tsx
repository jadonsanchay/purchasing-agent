import type { TraceEvent } from '../api'

const ACTOR_TONE: Record<string, string> = {
  agent: 'info',
  policy: 'warning',
  executor: 'neutral',
  validator: 'success',
  human: 'warning',
  system: 'neutral',
  erp: 'neutral',
}

function summarize(e: TraceEvent): string {
  const p = e.payload as Record<string, unknown>
  switch (e.event) {
    case 'tool_call':
      return `${p.tool}(${JSON.stringify(p.arguments)})`
    case 'decision_proposed':
      return `${String(p.action).toUpperCase()}${p.quantity ? ` ${p.quantity} units` : ''}${p.po_id ? ` on ${p.po_id}` : ''}`
    case 'gate_evaluated':
      return p.hard_fail ? 'blocked: hard constraint failed' : p.requires_approval ? 'passed, needs human approval' : 'passed'
    case 'actions_executed':
      return `${(p.actions as unknown[]).length} ERP action(s) executed`
    case 'validated':
      return p.ok ? 'outcome matches expectation' : `mismatch: ${(p.mismatches as string[]).join('; ')}`
    case 'recovery_turn':
      return `turn ${p.turn}: ${p.reason}`
    case 'awaiting_approval':
      return (p.reasons as string[]).join('; ')
    case 'run_completed':
    case 'escalated':
      return String(p.summary ?? '')
    case 'message':
      return String(p.text ?? '')
    default:
      return ''
  }
}

export function Trace({ trace }: { trace: TraceEvent[] }) {
  return (
    <div className="card">
      <div className="card-header">
        <h3>Trace</h3>
        <span className="muted small">{trace.length} events</span>
      </div>
      <div className="timeline">
        {trace.map((e, i) => (
          <div key={i} className="tl-item">
            <span className="tl-time">{e.at.slice(11, 19)}</span>
            <span className={`badge ${ACTOR_TONE[e.actor] ?? 'neutral'}`}>{e.actor}</span>
            <div className="tl-body">
              <div className="row">
                <span className="mono">{e.event}</span>
                <span className="small">{summarize(e)}</span>
              </div>
              <details>
                <summary>payload</summary>
                <pre>{JSON.stringify(e.payload, null, 2)}</pre>
              </details>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
