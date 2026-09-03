import { ArrowRight, Bot, CircleDollarSign, DatabaseZap, Gavel, ShieldCheck, TriangleAlert } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { compactMoney, countLabel, money, query, shortId } from "../api";
import { useApi } from "../hooks";
import type { CaseListResponse, OverviewResponse } from "../types";
import { Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";

const colors = ["#2c6e5b", "#2f6690", "#8a6a2f", "#a64942"];

export default function Overview() {
  const [params] = useSearchParams();
  const batch = params.get("batch");
  const navigate = useNavigate();
  const { data, error, loading, reload } = useApi<OverviewResponse>(`/api/overview${query(batch)}`);
  const { data: caseData } = useApi<CaseListResponse>(data?.selected_batch_id ? `/api/cases${query(data.selected_batch_id)}` : null);
  if (loading) return <LoadingState label="Loading reconciliation overview" />;
  if (error) return <ErrorState error={error} retry={reload} />;
  if (!data || !data.selected_batch_id) return <div className="page"><PageHeader title="Operations dashboard" description="A compact starting point for a fresh self-hosted installation." /><EmptyState title="No runs recorded yet" action={<Button onClick={() => navigate("/run")}>Load demo or run a batch</Button>}>Load the included demo to see real recorded outcomes, source health, and evidence review—or upload your own source files.</EmptyState></div>;
  const m = data.metrics;
  const chart = [
    { name: "Deterministic", value: m.deterministic_resolved },
    { name: "Evidence-assisted", value: m.ai_assisted_resolved },
    { name: "Human", value: m.human_resolved },
    { name: "Requires human review", value: m.needs_review },
  ].filter((item) => item.value > 0);
  return <div className="page">
    <PageHeader eyebrow="Operations command center" title="What happened in this run?" description="Outcome authority, review pressure, and source health for the active ledger batch." action={<Link className="button button-primary" to={`/reconciliation?batch=${encodeURIComponent(data.selected_batch_id)}`}>Review cases <ArrowRight size={15} /></Link>} />
    <Card className="authority-strip"><ShieldCheck size={19} /><div><strong>AI investigates. Deterministic controls decide.</strong><span>Every evidence-assisted outcome shown here passed deterministic validation and financial resolution policy.</span></div></Card>
      <section className="primary-metrics" aria-label="Primary reconciliation outcomes">
      <Card className="metric metric-main"><div className="metric-label"><CircleDollarSign size={17} /> Value represented</div><strong>{compactMoney(m.total_amount_paise)}</strong><span>{money(m.total_amount_paise)} across {countLabel(m.total_cases, "case")}</span>{caseData && <dl className="value-split"><div><dt>Resolved value</dt><dd>{compactMoney(caseData.cases.filter(item => item.status === "resolved").reduce((sum, item) => sum + item.amount_paise, 0))}</dd></div><div><dt>Held for review</dt><dd>{compactMoney(caseData.cases.filter(item => item.status === "needs_review").reduce((sum, item) => sum + item.amount_paise, 0))}</dd></div></dl>}</Card>
      <Card className="metric"><div className="metric-label"><ShieldCheck size={17} /> Deterministic</div><strong>{m.deterministic_resolved}</strong><span>Resolved without Stage 3</span></Card>
      <Card className="metric"><div className="metric-label"><Bot size={17} /> Evidence-assisted</div><strong>{m.ai_assisted_resolved}</strong><span>Evidence found; deterministic validation passed.</span></Card>
      <Card className="metric"><div className="metric-label"><TriangleAlert size={17} /> Requires human review</div><strong>{m.needs_review}</strong><span>Ambiguity retained safely</span></Card>
      </section>
      <section className="dashboard-shortcuts">
        <Link to={`/issues?batch=${encodeURIComponent(data.selected_batch_id)}`} className="dashboard-link-card"><DatabaseZap size={18} /><div><span>Source health</span><strong>{m.ingestion_issues} quarantined</strong><small>Source data problems stay outside reconciliation</small></div><ArrowRight size={16} /></Link>
        <Link to={`/reconciliation?batch=${encodeURIComponent(data.selected_batch_id)}&escalated=true`} className="dashboard-link-card"><TriangleAlert size={18} /><div><span>Attention needed</span><strong>{countLabel(m.needs_review, "review case")}</strong><small>Open the cases that safely remain unresolved</small></div><ArrowRight size={16} /></Link>
      </section>
    <section className="overview-grid">
      <Card className="distribution-card"><div className="section-heading"><div><span className="eyebrow">Outcome mix</span><h2>Resolution distribution</h2></div><span className="muted">{m.total_cases} total</span></div><div className="chart-wrap"><ResponsiveContainer width="52%" height={210}><PieChart><Pie data={chart} dataKey="value" nameKey="name" innerRadius={62} outerRadius={88} paddingAngle={2} stroke="none">{chart.map((_, i) => <Cell key={i} fill={colors[i]} />)}</Pie><Tooltip formatter={(value) => [String(value), "Cases"]} /></PieChart></ResponsiveContainer><div className="chart-legend">{chart.map((item, i) => <div key={item.name}><span style={{ background: colors[i] }} /><strong>{item.value}</strong><small>{item.name}</small></div>)}</div></div></Card>
      <Card className="operational-card"><div className="section-heading"><div><span className="eyebrow">Run telemetry</span><h2>Operational signals</h2></div></div><dl className="signal-list"><div><dt><ShieldCheck size={16} /> Automated resolutions</dt><dd>{m.deterministic_resolved + m.ai_assisted_resolved}</dd></div><div><dt><Gavel size={16} /> Human resolved</dt><dd>{m.human_resolved}</dd></div><div><dt><DatabaseZap size={16} /> Source issues quarantined</dt><dd>{m.ingestion_issues}</dd></div><div><dt><Bot size={16} /> Provider calls during this run</dt><dd>{m.provider_calls}</dd></div><div><dt>Recorded model tokens</dt><dd>{m.model_tokens?.toLocaleString("en-IN") ?? "Not reported"}</dd></div></dl></Card>
    </section>
    <Card className="recent-runs"><div className="section-heading"><div><span className="eyebrow">Ledger batches</span><h2>Recent runs</h2></div><Link to="/run" className="text-link">New run <ArrowRight size={14} /></Link></div><div className="run-list">{data.recent_runs.map((run) => <button key={run.batch_id} onClick={() => navigate(`/overview?batch=${encodeURIComponent(run.batch_id)}`)} className="run-row"><div><strong>{run.batch_id}</strong><span>{run.split} · fingerprint {shortId(run.content_fingerprint, 12)}</span></div><div className="run-outcomes"><span>{countLabel(run.metrics.total_cases, "case")}</span><span>{countLabel(run.metrics.needs_review, "review case")}</span><strong>{compactMoney(run.metrics.total_amount_paise)}</strong></div><ArrowRight size={16} /></button>)}</div></Card>
  </div>;
}
