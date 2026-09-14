export type Scenario = {
  id: string
  title: string
  kind: 'recommendation_review' | 'supplier_shortfall'
  description: string
  context: Record<string, string | number>
}

export type TraceEvent = { at: string; actor: string; event: string; payload: Record<string, unknown> }

export type Decision = {
  action: 'accept' | 'modify' | 'reject' | 'investigate' | 'escalate'
  quantity: number | null
  supplier_id: string | null
  po_id: string | null
  additional_orders: { supplier_id: string; quantity: number }[]
  rationale: string
  key_factors: string[]
  confidence: number
  expected_outcome: Record<string, unknown>
}

export type Check = { name: string; status: 'ok' | 'warn' | 'fail'; detail: string }
export type ConstraintReport = {
  proposed_qty: number
  supplier_id: string
  value: number
  checks: Check[]
  hard_fail: boolean
  fail_reasons: string[]
  requires_approval: boolean
  approval_reasons: string[]
  max_feasible_qty: number
  deviation_from_recommendation_pct: number | null
}

export type ExecutedAction = { kind: string; request: Record<string, unknown>; result: Record<string, unknown> & { ok?: boolean; reason?: string } }
export type Validation = { ok: boolean; expected: Record<string, unknown>; actual: Record<string, unknown>; mismatches: string[] }

export type RunStatus = 'running' | 'awaiting_approval' | 'completed' | 'escalated' | 'failed'
export type Run = {
  run_id: string
  scenario_id: string
  status: RunStatus
  situation: string
  trace: TraceEvent[]
  decision: Decision | null
  gate: { reports: ConstraintReport[]; hard_fail: boolean; requires_approval: boolean; approval_reasons: string[] } | null
  actions: ExecutedAction[]
  validations: Validation[]
  recovery_turns_used: number
  tool_calls: string[]
  final_summary: string | null
  error: string | null
  llm_usage: Record<string, number>
}

export type Health = { ok: boolean; llm_configured: boolean; model: string }

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => fetch('/api/health').then((r) => j<Health>(r)),
  scenarios: () => fetch('/api/scenarios').then((r) => j<Scenario[]>(r)),
  startRun: (scenario_id: string) =>
    fetch('/api/runs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ scenario_id }) }).then((r) => j<Run>(r)),
  getRun: (id: string) => fetch(`/api/runs/${id}`).then((r) => j<Run>(r)),
  approve: (id: string) => fetch(`/api/runs/${id}/approve`, { method: 'POST' }).then((r) => j<Run>(r)),
  reject: (id: string, note: string) =>
    fetch(`/api/runs/${id}/reject`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ note }) }).then((r) => j<Run>(r)),
}
