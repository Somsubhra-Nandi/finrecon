import { AlertOctagon, Database, FileWarning, ShieldX } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { query, shortId } from "../api";
import { useApi } from "../hooks";
import type { IngestionIssuesResponse } from "../types";
import { Card, EmptyState, ErrorState, JsonDetails, LoadingState, PageHeader } from "../components/ui";

export default function Issues() {
  const [params] = useSearchParams(); const batch = params.get("batch");
  const { data, error, loading, reload } = useApi<IngestionIssuesResponse>(`/api/ingestion/issues${query(batch)}`);
  return <div className="page">
    <PageHeader eyebrow="Source controls" title="Ingestion issues" description="Rows and settlements excluded before reconciliation. These are source-data problems, not ambiguous financial decisions." />
    <Card className="source-warning"><ShieldX size={21} /><div><strong>Source data problem ≠ reconciliation ambiguity</strong><span>Quarantined or rejected data never reaches the candidate queue and cannot receive a human reconciliation resolution.</span></div></Card>
    {loading ? <LoadingState label="Loading ingestion audit" /> : error ? <ErrorState error={error} retry={reload} /> : !data?.issues.length ? <EmptyState icon={<Database size={21} />} title="No ingestion issues">Every submitted source record cleared its declared adapter boundary for this batch.</EmptyState> : <div className="issue-sections">
      {(["razorpay", "bank"] as const).map((source) => { const issues = data.issues.filter((item) => item.source_kind === source); if (!issues.length) return null; return <section key={source}><div className="issue-heading"><div className={`source-icon ${source}`}>{source === "razorpay" ? <AlertOctagon size={19} /> : <FileWarning size={19} />}</div><div><span className="eyebrow">{source === "razorpay" ? "Razorpay adapter" : "Bank CSV adapter"}</span><h2>{source === "razorpay" ? "Quarantined settlements" : "Rejected bank rows"}</h2></div><span>{issues.length} issue{issues.length === 1 ? "" : "s"}</span></div><div className="issue-list">{issues.map((item) => <Card key={item.event_id} className="issue-card"><div className="issue-summary"><div><span className="issue-type">Source data problem</span><h3>{item.problem.replaceAll("_", " ")}</h3><p>{item.detail ?? (source === "razorpay" ? "Conflicting source facts prevented this settlement from becoming decision-eligible." : "The declared bank profile could not canonicalize this row safely.")}</p></div><dl><div><dt>{source === "razorpay" ? "Settlement ID" : "Row"}</dt><dd>{item.subject_id ?? "—"}</dd></div><div><dt>Source</dt><dd>{item.source_id}</dd></div><div><dt>Fingerprint</dt><dd title={item.fingerprint}>{shortId(item.fingerprint, 18)}</dd></div></dl></div><JsonDetails label="Provenance and adapter finding" value={item.payload} /></Card>)}</div></section>; })}
    </div>}
  </div>;
}
