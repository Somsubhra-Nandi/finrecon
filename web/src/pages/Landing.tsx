import { ArrowRight, BadgeCheck, BookOpen, FileSearch, Gavel, Landmark, ShieldCheck, TriangleAlert } from "lucide-react";
import { Link } from "react-router-dom";

const pipeline = [
  { label: "Razorpay + Bank", detail: "Source records" },
  { label: "Deterministic matching", detail: "Known financial facts" },
  { label: "Bounded evidence search", detail: "AI investigates, within limits" },
  { label: "Deterministic validation", detail: "Evidence and policy gates" },
];

export default function Landing() {
  return <div className="landing">
    <header className="landing-nav">
      <Link className="landing-brand" to="/" aria-label="FinRecon home"><span><Gavel size={18} /></span><strong>FinRecon</strong></Link>
      <nav aria-label="Landing navigation"><Link to="/benchmarks">Benchmarks</Link><Link to="/overview">Operations console</Link></nav>
    </header>

    <main>
      <section className="landing-hero">
        <div className="landing-copy">
          <span className="landing-kicker"><ShieldCheck size={14} /> Financial reconciliation controls</span>
          <h1>Reconcile with evidence.<br /><em>Escalate uncertainty.</em></h1>
          <p>FinRecon uses bounded AI evidence search to investigate unresolved cases. Deterministic financial validation and policy retain authority over every decision.</p>
          <div className="landing-actions"><Link className="landing-primary" to="/run">Explore Demo <ArrowRight size={16} /></Link><Link className="landing-secondary" to="/benchmarks">View Benchmarks <BookOpen size={16} /></Link></div>
          <p className="landing-assurance"><BadgeCheck size={15} /> No model confidence or prose can determine money movement.</p>
        </div>
        <div className="control-panel" aria-label="FinRecon authority pipeline">
          <div className="panel-heading"><span>CONTROL PATH</span><strong>Financial authority remains deterministic</strong></div>
          <div className="pipeline-list">{pipeline.map((item, index) => <div className="pipeline-stage" key={item.label}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.label}</strong><small>{item.detail}</small></div>{index < pipeline.length - 1 && <i />}</div>)}</div>
          <div className="pipeline-outcomes"><div><BadgeCheck size={18} /><strong>RESOLVED</strong><span>Validation passed</span></div><div><TriangleAlert size={18} /><strong>ESCALATED</strong><span>Uncertainty retained</span></div></div>
        </div>
      </section>

      <section className="landing-principle"><span>AI investigates.</span><ArrowRight size={17} /><strong>Deterministic controls decide.</strong><p>There is no path from LLM confidence to money.</p></section>

      <section className="proof-section" aria-labelledby="proof-title">
        <div><span className="landing-kicker">Frozen evaluation evidence</span><h2 id="proof-title">Safety is a product property.</h2><p>FinRecon’s current full-pipeline frozen evaluation tests whether the system resolves only when the evidence and policy gates support it.</p><Link className="inline-link" to="/benchmarks?benchmark=frozen-eval-v3">Inspect the frozen report <ArrowRight size={15} /></Link></div>
        <div className="proof-metrics"><div><strong>890</strong><span>frozen evaluation cases</span></div><div><strong>0</strong><span>wrong automatic resolutions</span></div><div><strong>0</strong><span>unsafe auto-matches</span></div><div><strong>₹0</strong><span>value at risk</span></div></div>
      </section>

      <section className="model-section" aria-labelledby="model-title">
        <div className="model-intro"><span className="landing-kicker">Bounded-search evidence</span><h2 id="model-title">Better search improves resolution. It does not gain authority.</h2><p>Recorded bounded-search cohorts show that weaker/free model responses produced more protocol failures and lower resolution. Stronger capability improved search discipline—but every automatic result still had to pass the same deterministic controls.</p></div>
        <div className="cohort-grid"><article><span>OPENROUTER FREE</span><strong>45-case valid scored cohort</strong><p>30 correct automatic resolutions; 15 escalations; 11 tool-validation failures. Zero wrong automatic resolutions and ₹0 value at risk.</p></article><article><span>OPUS</span><strong>40-case successfully answered cohort</strong><p>31 correct automatic resolutions; 9 escalations; no tool-validation failures. Zero wrong automatic resolutions and ₹0 value at risk.</p></article></div>
        <p className="cohort-note">These are differently sized scored cohorts, not an apples-to-apples causal comparison. See the recorded reports and trajectories for denominators, requested/reported models, and provider metadata.</p>
      </section>

      <section className="landing-console-cta"><div><Landmark size={21} /><span className="landing-kicker">Operations console</span><h2>Inspect the evidence chain, not just the outcome.</h2><p>Review source facts, immutable candidate snapshots, validation results, policy outcomes, recorded trajectories, and audit history.</p></div><Link className="landing-primary" to="/overview">Enter Operations Console <FileSearch size={16} /></Link></section>
    </main>

    <footer className="landing-footer"><strong>FinRecon</strong><span>Bounded AI authority for financial reconciliation.</span><Link to="/benchmarks">Offline benchmark replay</Link></footer>
  </div>;
}
