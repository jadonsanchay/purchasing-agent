import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, type Health, type Run, type Scenario } from './api'
import { Badge, StatusBadge } from './components/Badge'
import { DecisionCard } from './components/DecisionCard'
import { GateCard } from './components/GateCard'
import { OutcomeCard } from './components/OutcomeCard'
import { Trace } from './components/Trace'

const ACTIVE = new Set(['running'])

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    // deep link: /?run=<run_id> opens an existing run (used for demos and sharing a trace)
    const linked = new URLSearchParams(window.location.search).get('run')
    api
      .scenarios()
      .then((s) => {
        setScenarios(s)
        setSelected(s[0]?.id ?? null)
        if (linked) {
          api
            .getRun(linked)
            .then((r) => {
              setRun(r)
              setSelected(r.scenario_id)
            })
            .catch((e) => setError((e as Error).message))
        }
      })
      .catch((e) => setError((e as Error).message))
  }, [])

  useEffect(() => {
    // /?run=<id>#gate scrolls to a card once the run has rendered (used by the demo capture)
    const anchor = window.location.hash.slice(1)
    if (run && anchor) document.getElementById(anchor)?.scrollIntoView()
  }, [run])

  useEffect(() => {
    if (!run || !ACTIVE.has(run.status)) return
    const t = setInterval(() => api.getRun(run.run_id).then(setRun).catch(() => undefined), 1200)
    return () => clearInterval(t)
  }, [run])

  const start = useCallback(async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    try {
      setRun(await api.startRun(selected))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [selected])

  const act = async (fn: () => Promise<Run>) => {
    setBusy(true)
    try {
      setRun(await fn())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const scenario = scenarios.find((s) => s.id === selected)

  return (
    <div className="app">
      <header className="topbar">
        <div className="row">
          <h1>AI Purchasing Agent</h1>
          <span className="sub small">investigate · decide · execute · validate</span>
        </div>
        <div className="row">
          {health ? (
            health.llm_configured ? (
              <Badge tone="neutral">model {health.model}</Badge>
            ) : (
              <Badge tone="warning">OPENAI_API_KEY not set</Badge>
            )
          ) : (
            <Badge tone="danger">backend offline</Badge>
          )}
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <div className="stack">
            <h3>Scenario 1 · Recommendation review</h3>
            {scenarios.filter((s) => s.kind === 'recommendation_review').map((s) => (
              <ScenarioButton key={s.id} s={s} active={s.id === selected} onClick={() => setSelected(s.id)} />
            ))}
          </div>
          <div className="stack">
            <h3>Scenario 2 · Supplier short-fill</h3>
            {scenarios.filter((s) => s.kind === 'supplier_shortfall').map((s) => (
              <ScenarioButton key={s.id} s={s} active={s.id === selected} onClick={() => setSelected(s.id)} />
            ))}
          </div>
        </aside>

        <main className="main">
          {scenario && (
            <div className="card">
              <div className="card-header">
                <div className="stack">
                  <h2>
                    {scenario.id} · {scenario.title}
                  </h2>
                  <p className="muted small">{scenario.description}</p>
                </div>
                <button className={`btn ${run?.status === 'awaiting_approval' ? '' : 'primary'}`} disabled={busy || (run !== null && ACTIVE.has(run.status))} onClick={start}>
                  {run && run.scenario_id === scenario.id ? 'Run again' : 'Run agent'}
                </button>
              </div>
              <dl className="kv small">
                {Object.entries(scenario.context).map(([k, v]) => (
                  <Fragment key={k}>
                    <dt>{k}</dt>
                    <dd className="mono">{String(v)}</dd>
                  </Fragment>
                ))}
              </dl>
            </div>
          )}

          {error && (
            <div className="banner danger">
              <strong>Could not start run</strong>
              <span className="small">{error}</span>
            </div>
          )}

          {!run && !error && <div className="empty">Pick a scenario and run the agent. Loading a scenario resets the mock ERP so every run is reproducible.</div>}

          {run && (
            <>
              <div className="card">
                <div className="card-header">
                  <div className="row">
                    <StatusBadge status={run.status} />
                    <span className="mono muted small">{run.run_id}</span>
                  </div>
                  <div className="row small muted">
                    <span>{run.tool_calls.length} tool calls</span>
                    {run.llm_usage.input_tokens !== undefined && <span>· {run.llm_usage.input_tokens + (run.llm_usage.output_tokens ?? 0)} tokens</span>}
                  </div>
                </div>
                <p className="small">{run.situation}</p>
                {run.final_summary && <div className={`banner ${run.status === 'escalated' ? 'danger' : 'success'}`}>{run.final_summary}</div>}
                {run.error && <div className="banner danger mono small">{run.error}</div>}
              </div>

              <div id="decision" />
              <DecisionCard run={run} />
              <div id="gate" />
              <GateCard run={run} busy={busy} onApprove={() => act(() => api.approve(run.run_id))} onReject={(note) => act(() => api.reject(run.run_id, note))} />
              <div id="outcome" />
              <OutcomeCard run={run} />
              <div id="trace" />
              <Trace trace={run.trace} />
            </>
          )}
        </main>
      </div>
    </div>
  )
}

function ScenarioButton({ s, active, onClick }: { s: Scenario; active: boolean; onClick: () => void }) {
  return (
    <button className={`scenario ${active ? 'active' : ''}`} onClick={onClick}>
      <span className="title">
        {s.id} · {s.title.split(': ')[1] ?? s.title}
      </span>
      <span className="desc">{s.description}</span>
    </button>
  )
}
