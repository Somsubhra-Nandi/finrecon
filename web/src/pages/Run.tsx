import { AlertTriangle, ArchiveRestore, ArrowRight, BadgeCheck, Check, FileJson, FileSpreadsheet, FlaskConical, KeyRound, Play, Search, SlidersHorizontal, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import type { BankStatementInspection, BuiltInProfileView, RunResponse } from "../types";
import { Button, Card, PageHeader } from "../components/ui";

function FileField({ label, accept, file, onChange, icon }: { label: string; accept: string; file: File | null; onChange: (file: File | null) => void; icon: React.ReactNode }) {
  const ref = useRef<HTMLInputElement>(null);
  return <button type="button" className={`file-field ${file ? "has-file" : ""}`} onClick={() => ref.current?.click()}><input ref={ref} className="sr-only" type="file" accept={accept} onChange={(e) => onChange(e.target.files?.[0] ?? null)} /><span className="file-icon">{file ? <Check size={18} /> : icon}</span><span><strong>{label}</strong><small>{file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "Choose a file"}</small></span><UploadCloud size={17} /></button>;
}

const TIER_LABEL: Record<string, string> = {
  exact: "Exact schema match",
  safe_normalized: "Normalized schema match (spacing/case only)",
};

// Surfaced verbatim rather than collapsed into a "supported" badge: an
// operator about to reconcile money is entitled to know how well a
// profile's schema is actually evidenced.
const VERIFICATION_LABEL: Record<string, string> = {
  vendor_verified: "Vendor-verified schema",
  partially_verified: "Partially verified schema",
  demo_fixture: "Demo fixture — synthetic schema, not a real bank",
};

function ProfileLine({ profile }: { profile: BuiltInProfileView }) {
  return <div className="profile-line"><strong>{profile.label}</strong><small>{profile.profile_id} · {profile.version} · {VERIFICATION_LABEL[profile.verification] ?? profile.verification}</small></div>;
}

export default function Run() {
  const navigate = useNavigate();
  const [razorpay, setRazorpay] = useState<File | null>(null);
  const [bank, setBank] = useState<File | null>(null);
  const [profile, setProfile] = useState<File | null>(null);
  const [mode, setMode] = useState<"replay" | "live">("replay");
  const [batchId, setBatchId] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [liveUnavailable, setLiveUnavailable] = useState(false);
  const [inspection, setInspection] = useState<BankStatementInspection | null>(null);
  const [inspecting, setInspecting] = useState(false);
  // Explicitly overriding a recognised format. Never set automatically:
  // switching away from a detected profile is always the operator's call.
  const [manualOverride, setManualOverride] = useState(false);

  const detected = !manualOverride && inspection?.status === "matched" ? inspection.profile : null;
  const needsManualProfile = !detected;
  const canRun = Boolean(razorpay && bank && (detected || profile));

  const complete = (result: RunResponse) => navigate(`/overview?batch=${encodeURIComponent(result.batch_id)}`);

  // Inspection is read-only on the server: it creates no batch, writes no
  // ledger state and calls no provider, so running it the moment a bank
  // file is chosen commits the operator to nothing.
  const chooseBank = async (file: File | null) => {
    setBank(file); setInspection(null); setManualOverride(false); setError(null);
    if (!file) return;
    setInspecting(true);
    try {
      const body = new FormData(); body.set("bank_file", file);
      setInspection(await api<BankStatementInspection>("/api/bank-statement/inspect", { method: "POST", body }));
    } catch (e) {
      // A failed inspection is never an inferred match: fall through to
      // the manual-profile path exactly as an unrecognised file does.
      setInspection(null);
      setError(e instanceof ApiError ? e.message : "The bank statement could not be inspected.");
    } finally { setInspecting(false); }
  };

  const runDemo = async () => { setRunning(true); setError(null); setLiveUnavailable(false); setStatus("Loading demo fixtures and replay trajectories…"); try { const result = await api<RunResponse>("/api/reconciliation/demo", { method: "POST" }); complete(result); } catch (e) { setError(e instanceof ApiError ? e.message : "The demo batch failed."); } finally { setRunning(false); setStatus(""); } };

  const runUpload = async () => {
    if (!razorpay || !bank || !(detected || profile)) return;
    setRunning(true); setError(null); setLiveUnavailable(false);
    setStatus(mode === "replay" ? "Running deterministic stages and replaying cached investigations…" : "Running deterministic stages; unresolved cases may contact configured providers…");
    const body = new FormData();
    body.set("razorpay_file", razorpay); body.set("bank_file", bank);
    // Exactly one profile source. The server re-verifies a built-in id
    // against these bytes before ingesting anything, so this is a request,
    // not an authorization.
    if (detected) body.set("built_in_profile_id", detected.profile_id);
    else if (profile) body.set("bank_profile", profile);
    body.set("mode", mode);
    if (batchId.trim()) body.set("batch_id", batchId.trim());
    try { complete(await api<RunResponse>("/api/reconciliation/run", { method: "POST", body })); }
    catch (e) { setLiveUnavailable(e instanceof ApiError && e.code === "live_provider_not_configured"); setError(e instanceof ApiError ? e.message : "The reconciliation run failed."); }
    finally { setRunning(false); setStatus(""); }
  };

  return <div className="page run-page"><PageHeader eyebrow="New batch" title="Run reconciliation" description="Submit source data to the existing deterministic and bounded-investigation pipeline. Provider secrets stay on the server." />
    <div className="run-layout"><div className="run-main">
      <Card><div className="section-heading"><div><span className="step-label">01</span><h2>Source files</h2><p>Files are parsed by their existing adapters. Invalid source records are audited separately.</p></div></div>
        <div className="file-grid file-grid-two"><FileField label="Razorpay recon file" accept=".json,application/json" file={razorpay} onChange={setRazorpay} icon={<FileJson size={18} />} /><FileField label="Bank statement (CSV)" accept=".csv,text/csv" file={bank} onChange={chooseBank} icon={<FileSpreadsheet size={18} />} /></div>

        {inspecting && <div className="schema-panel" role="status"><Search size={15} /> <span>Inspecting bank statement schema…</span></div>}

        {!inspecting && inspection?.status === "matched" && inspection.profile && !manualOverride &&
          <div className="schema-panel schema-matched" role="status">
            <BadgeCheck size={16} />
            <div><strong>Bank format recognized</strong>
              <ProfileLine profile={inspection.profile} />
              <small>{TIER_LABEL[inspection.match_tier ?? ""] ?? "Schema match"} · no bank profile JSON needed</small>
            </div>
            <Button variant="quiet" onClick={() => setManualOverride(true)}>Change / use manual profile</Button>
          </div>}

        {!inspecting && inspection?.status === "ambiguous" &&
          <div className="schema-panel schema-ambiguous" role="alert">
            <AlertTriangle size={16} />
            <div><strong>Multiple known bank formats match this statement</strong>
              <small>FinRecon will not choose between them. Upload the bank profile JSON for the correct format below.</small>
              <ul className="schema-candidates">{inspection.candidates.map((candidate) => <li key={candidate.profile_id}><ProfileLine profile={candidate} /></li>)}</ul>
            </div>
          </div>}

        {!inspecting && inspection?.status === "unknown" &&
          <div className="schema-panel schema-unknown" role="status">
            <AlertTriangle size={16} />
            <div><strong>Bank format not recognized</strong>
              <small>FinRecon does not guess column meanings. Upload a bank profile JSON that declares this statement's columns.</small>
              {inspection.raw_headers.length > 0 && <small className="schema-headers">Columns read: {inspection.raw_headers.join(" · ")}</small>}
            </div>
          </div>}

        {needsManualProfile &&
          <details className="manual-profile" open={Boolean(bank) && inspection?.status !== "matched"}>
            <summary><SlidersHorizontal size={14} /> Advanced · Manual bank profile</summary>
            <p>A bank profile JSON declares the statement's columns, date format and debit/credit convention explicitly. Always available, and required whenever a format is unrecognized or ambiguous.</p>
            <FileField label="Bank profile / config" accept=".json,application/json" file={profile} onChange={setProfile} icon={<FileJson size={18} />} />
          </details>}

        <label className="field-label">Batch ID <span>Optional; generated when blank</span><input value={batchId} onChange={(e) => setBatchId(e.target.value)} placeholder="batch:august-settlement-run" /></label>
      </Card>

      <Card><div className="section-heading"><div><span className="step-label">02</span><h2>Execution mode</h2><p>Stage 2 is deterministic in either mode. The difference applies only to unresolved Stage 3 cases.</p></div></div><div className="mode-grid"><button className={mode === "replay" ? "selected" : ""} onClick={() => setMode("replay")}><ArchiveRestore size={20} /><div><strong>Replay / Cached</strong><span>No paid or provider calls. Missing trajectories fail closed.</span></div><span className="mode-radio" /></button><button className={mode === "live" ? "selected" : ""} onClick={() => setMode("live")}><KeyRound size={20} /><div><strong>Live</strong><span>Unresolved Stage 3 cases may use server-configured providers.</span></div><span className="mode-radio" /></button></div><div className="secret-note"><KeyRound size={15} /> Credentials are read only from the backend environment and never sent to this browser.</div></Card>

      {error && <div className="run-error" role="alert">{liveUnavailable ? <><strong>Live investigation requires provider credentials on the self-hosted server.</strong><p>FinRecon never accepts provider secrets through the browser. Configure one or more backend environment variables—`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, or `GOROUTER_API_KEY`—as documented in `.env.example`, then restart the server.</p></> : error}</div>}
      {status && <div className="run-progress" role="status"><span className="progress-track"><i /></span><strong>Reconciliation in progress</strong><small>{status}</small></div>}

      <Button className="run-submit" disabled={running || inspecting || !canRun} onClick={runUpload}><Play size={16} /> {running ? "Running…" : "Run reconciliation"}</Button></div>
      <aside><Card className="demo-card"><div className="demo-icon"><FlaskConical size={22} /></div><span className="eyebrow">Judge-ready path</span><h2>Load demo batch</h2><p>Runs real adapters, Stage 2, cached Stage 3 replay, validation, policy, and SQLite persistence.</p><ul><li><Check size={14} /> Deterministic resolution</li><li><Check size={14} /> AI-assisted replay resolution</li><li><Check size={14} /> True ambiguity and human review</li><li><Check size={14} /> Separate ingestion quarantine</li></ul><Button variant="secondary" disabled={running} onClick={runDemo}>Load demo batch <ArrowRight size={15} /></Button><small>Zero network/provider calls</small></Card></aside>
    </div>
  </div>;
}
