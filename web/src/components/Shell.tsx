import { Activity, AlertTriangle, FileSearch, LayoutDashboard, Play, Scale, Trophy } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { useApi } from "../hooks";
import type { RunSummary } from "../types";

const navigation = [
  { group: "Operations", items: [{ to: "/overview", label: "Dashboard", icon: LayoutDashboard }, { to: "/reconciliation", label: "Reconciliation", icon: FileSearch }, { to: "/issues", label: "Source issues", icon: AlertTriangle }] },
  { group: "Run", items: [{ to: "/run", label: "New reconciliation", icon: Play }] },
  { group: "Evaluation", items: [{ to: "/benchmarks", label: "Evidence & Evaluation", icon: Trophy }] },
];

export default function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  const batch = new URLSearchParams(location.search).get("batch");
  const { data: runs } = useApi<RunSummary[]>("/api/runs");
  const activeBatch = batch ?? runs?.[0]?.batch_id ?? null;
  const isBenchmark = location.pathname.startsWith("/benchmarks");
  const suffix = batch ? `?batch=${encodeURIComponent(batch)}` : "";
  const selectBatch = (batchId: string) => {
    const destination = location.pathname.startsWith("/reconciliation/") ? "/reconciliation" : location.pathname;
    const next = new URLSearchParams(location.search);
    next.set("batch", batchId);
    navigate(`${destination}?${next.toString()}`);
  };
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Scale size={19} /></div><div><strong>FinRecon</strong><span>Operations console</span></div></div>
      <nav aria-label="Primary navigation">
        {navigation.map(({ group, items }) => <div className="nav-group" key={group}><span>{group}</span>{items.map(({ to, label, icon: Icon }) => <NavLink key={to} to={`${to}${suffix}`} end={to === "/overview"} className={({ isActive }) => clsx("nav-item", isActive && "active")}><Icon size={17} /><span>{label}</span></NavLink>)}</div>)}
      </nav>
      <div className="sidebar-note"><Activity size={16} /><div><strong>Financial authority</strong><span>Deterministic validation</span></div></div>
    </aside>
    <div className="workspace">
      <div className="topbar"><div className="environment"><span /> {isBenchmark ? "Offline evaluation" : "Ledger connected"}</div>{isBenchmark ? <div className="batch-context"><span>Zero provider calls</span><strong>Recorded artifacts</strong></div> : <div className="batch-context"><span>Active batch</span>{runs && runs.length > 1 ? <select aria-label="Active reconciliation batch" value={activeBatch ?? ""} onChange={(event) => selectBatch(event.target.value)}>{runs.map((run) => <option key={run.batch_id} value={run.batch_id}>{run.batch_id}</option>)}</select> : <strong title={activeBatch ?? "Latest recorded"}>{activeBatch ?? "Latest recorded"}</strong>}</div>}</div>
      <main><Outlet /></main>
    </div>
  </div>;
}
