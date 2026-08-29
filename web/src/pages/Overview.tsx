import { ArrowRight, Bot, CircleDollarSign, DatabaseZap, Gavel, ShieldCheck, TriangleAlert } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { compactMoney, money, query, shortId } from "../api";
import { useApi } from "../hooks";
import type { OverviewResponse } from "../types";
import { Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";

const colors = ["#2c6e5b", "#2f6690", "#8a6a2f", "#a64942"];

export default function Overview() {
  const [params] = useSearchParams();
  const batch = params.get("batch");
  const navigate = useNavigate();
  const { data, error, loading, reload } = useApi<OverviewResponse>(`/api/overview${query(batch)}`);
  if (loading) return <LoadingState label="Loading reconciliation overview" />;
  if (error) return <ErrorState error={error} retry={reload} />;
  if (!data || !data.selected_batch_id) return <div className="page"><PageHeader title="Reconciliation overview" description="Operational outcomes across the active ledger batch." /><EmptyState title="No runs recorded yet" action={<Button onClick={() => navigate("/run")}>Run your first batch</Button>}>Upload source files or load the demo batch to populate the operations console.</EmptyState></div>;
  const m = data.metrics;
  const chart = [
    { name: "Deterministic", value: m.deterministic_resolved },
    { name: "AI-assisted", value: m.ai_assisted_resolved },
    { name: "Human", value: m.human_resolved },
    { name: "Needs review", value: m.needs_review },
  ].filter((item) => item.value > 0);
  return <div className="page">
    <PageHeader eyebrow="Operations summary" title="Reconciliation overview" description="Outcome authority, value represented, and review pressure for the active batch." action={<Link className="button button-primary" to={`/reconciliation?batch=${encodeURIComponent(data.selected_batch_id)}`}>Open queue <ArrowRight size={15} /></Link>} />
    <Card className="authority-strip"><ShieldCheck size={19} /><div><strong>AI searches for evidence. Deterministic validation retains financial authority.</strong><span>Every AI-assisted outcome shown here passed the same mechanical validator and policy gate.</span></div></Card>
    <section className="primary-metrics" aria-label="Primary reconciliation outcomes">
      <Card className="metric metric-main"><div className="metric-label"><CircleDollarSign size={17} /> Value represented</div><strong>{compactMoney(m.total_amount_paise)}</strong><span>{money(m.total_amount_paise)} across {m.total_cases} cases</span></Card>
      <Card className="metric"><div className="metric-label"><ShieldCheck size={17} /> Deterministic</div><strong>{m.deterministic_resolved}</strong><span>Resolved without Stage 3</span></Card>
      <Card className="metric"><div className="metric-label"><Bot size={17} /> AI-assisted</div><strong>{m.ai_assisted_resolved}</strong><span>Evidence found, validation passed</span></Card>
      <Card className="metric"><div className="metric-label"><TriangleAlert size={17} /> Needs review</div><strong>{m.needs_review}</strong><span>Ambiguity retained safely</span></Card>
    </section>
    <section className="overview-grid">
      <Card className="distribution-card"><div className="section-heading"><div><span className="eyebrow">Outcome mix</span><h2>Resolution distribution</h2></div><span className="muted">{m.total_cases} total</span></div><div className="chart-wrap"><ResponsiveContainer width="52%" height={210}><PieChart><Pie data={chart} dataKey="value" nameKey="name" innerRadius={62} outerRadius={88} paddingAngle={2} stroke="none">{chart.map((_, i) => <Cell key={i} fill={colors[i]} />)}</Pie><Tooltip formatter={(value) => [String(value), "Cases"]} /></PieChart></ResponsiveContainer><div className="chart-legend">{chart.map((item, i) => <div key={item.name}><span style={{ background: colors[i] }} /><strong>{item.value}</strong><small>{item.name}</small></div>)}</div></div></Card>
      <Card className="operational-card"><div className="section-heading"><div><span className="eyebrow">Run telemetry</span><h2>Operational signals</h2></div></div><dl className="signal-list"><div><dt><ShieldCheck size={16} /> Automated resolutions</dt><dd>{m.deterministic_resolved + m.ai_assisted_resolved}</dd></div><div><dt><Gavel size={16} /> Human resolved</dt><dd>{m.human_resolved}</dd></div><div><dt><DatabaseZap size={16} /> Source issues quarantined</dt><dd>{m.ingestion_issues}</dd></div><div><dt><Bot size={16} /> Provider calls</dt><dd>{m.provider_calls}</dd></div><div><dt>Model tokens reported</dt><dd>{m.model_tokens?.toLocaleString("en-IN") ?? "Not reported"}</dd></div></dl></Card>
    </section>
    <Card className="recent-runs"><div className="section-heading"><div><span className="eyebrow">Ledger batches</span><h2>Recent runs</h2></div><Link to="/run" className="text-link">New run <ArrowRight size={14} /></Link></div><div className="run-list">{data.recent_runs.map((run) => <button key={run.batch_id} onClick={() => navigate(`/overview?batch=${encodeURIComponent(run.batch_id)}`)} className="run-row"><div><strong>{run.batch_id}</strong><span>{run.split} · fingerprint {shortId(run.content_fingerprint, 12)}</span></div><div className="run-outcomes"><span>{run.metrics.total_cases} cases</span><span>{run.metrics.needs_review} review</span><strong>{compactMoney(run.metrics.total_amount_paise)}</strong></div><ArrowRight size={16} /></button>)}</div></Card>
  </div>;
}
