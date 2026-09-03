import { AlertTriangle, ArchiveRestore, ArrowRight, BadgeCheck, Check, FileJson, FileSpreadsheet, FlaskConical, KeyRound, Pencil, Play, Search, SlidersHorizontal, Sparkles, UploadCloud, UserCheck } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import type { BankMappingProposalResponse, BankMappingSaveResponse, BankStatementInspection, BuiltInProfileView, MappingDraft, MappingMatchView, MappingValidationView, RunResponse, SavedMappingView } from "../types";
import { Button, Card, PageHeader } from "../components/ui";
import MappingEditor, { draftFromProposal, draftFromSavedMapping, draftToRequest, EMPTY_DRAFT, SamplePreview } from "../components/MappingEditor";

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

/**
 * One matched entry, of either kind. A saved mapping states who confirmed it
 * where a built-in states an evidence grade -- the two claims are different
 * and neither is dressed up as the other.
 */
function MatchLine({ match }: { match: MappingMatchView }) {
  if (match.kind === "user_saved") {
    return <div className="profile-line"><strong>{match.label}</strong><small>{match.version} · Saved mapping · human-confirmed</small></div>;
  }
  return <div className="profile-line"><strong>{match.label}</strong><small>{match.profile_id} · {match.version} · {VERIFICATION_LABEL[match.verification ?? ""] ?? match.verification}</small></div>;
}

type Stage = "idle" | "review" | "saved";

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

  // --- unknown-schema mapping flow ---------------------------------------
  const [stage, setStage] = useState<Stage>("idle");
  const [proposing, setProposing] = useState(false);
  const [proposalResponse, setProposalResponse] = useState<BankMappingProposalResponse | null>(null);
  const [draft, setDraft] = useState<MappingDraft>(EMPTY_DRAFT);
  const [validation, setValidation] = useState<MappingValidationView | null>(null);
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [savedMapping, setSavedMapping] = useState<SavedMappingView | null>(null);
  const [savingMapping, setSavingMapping] = useState(false);
  const [mappingError, setMappingError] = useState<string | null>(null);
  // Set when editing a mapping that already exists: the save then targets
  // that mapping's versions endpoint, creating v(n+1) rather than a second
  // mapping with a near-duplicate name.
  const [editingMappingId, setEditingMappingId] = useState<string | null>(null);

  // `match` is the field that sees both kinds; `profile` is the older
  // built-in-only field. Reading `match ?? profile` rather than `match`
  // alone keeps this page correct against a server that predates saved
  // mappings, and is the same tolerance the response was designed for.
  const matchFromInspection = (): MappingMatchView | null => {
    if (inspection?.match) return inspection.match;
    if (!inspection?.profile) return null;
    const builtIn = inspection.profile;
    return {
      kind: "built_in", profile_id: builtIn.profile_id, label: builtIn.label,
      version: builtIn.version, verification: builtIn.verification,
      description: builtIn.description, evidence: builtIn.evidence,
      saved_mapping: null,
    };
  };
  const detectedMatch = !manualOverride && inspection?.status === "matched" ? matchFromInspection() : null;
  // The tie list, under the same two-field tolerance.
  const ambiguousMatches = (): MappingMatchView[] => {
    if (inspection?.matches?.length) return inspection.matches;
    return (inspection?.candidates ?? []).map((builtIn) => ({
      kind: "built_in" as const, profile_id: builtIn.profile_id, label: builtIn.label,
      version: builtIn.version, verification: builtIn.verification,
      description: builtIn.description, evidence: builtIn.evidence,
      saved_mapping: null,
    }));
  };
  const detectedBuiltIn = detectedMatch?.kind === "built_in" ? detectedMatch : null;
  const detectedSaved = detectedMatch?.kind === "user_saved" ? detectedMatch.saved_mapping : null;
  // A mapping confirmed in this session is used the same way a recognised
  // one is: by id, re-verified server-side. Nothing about "I just saved it"
  // is trusted beyond holding the id.
  const activeSaved = savedMapping ?? detectedSaved;
  const needsManualProfile = !detectedMatch && stage !== "saved" && !savedMapping;
  const canRun = Boolean(razorpay && bank && (detectedBuiltIn || activeSaved || profile));

  const complete = (result: RunResponse) => navigate(`/overview?batch=${encodeURIComponent(result.batch_id)}`);

  const resetMapping = () => {
    setStage("idle"); setProposalResponse(null); setDraft(EMPTY_DRAFT);
    setValidation(null); setAcknowledged([]); setSavedMapping(null);
    setMappingError(null); setEditingMappingId(null);
  };

  // Inspection is read-only on the server: it creates no batch, writes no
  // ledger state and calls no provider, so running it the moment a bank
  // file is chosen commits the operator to nothing.
  const chooseBank = async (file: File | null) => {
    setBank(file); setInspection(null); setManualOverride(false); setError(null);
    resetMapping();
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

  /**
   * Open the mapping editor. Asks the server for a proposal first; a failed
   * or unavailable proposal is not an error state, it simply opens the same
   * editor empty. The product does not stop working when a model does.
   */
  const openMappingReview = async (options: { editing?: SavedMappingView | null } = {}) => {
    if (!bank) return;
    const editing = options.editing ?? null;
    setStage("review"); setMappingError(null); setAcknowledged([]);
    setEditingMappingId(editing?.mapping_id ?? null);
    setProposing(true);
    try {
      const body = new FormData(); body.set("bank_file", bank);
      // The same endpoint serves both cases, and it decides server-side
      // whether a model is worth contacting: a file whose schema is already
      // recognised (which is exactly the situation when editing a saved
      // mapping) returns headers, the sample and the format list with no
      // provider call at all.
      const response = await api<BankMappingProposalResponse>("/api/bank-mappings/propose", { method: "POST", body });
      setProposalResponse(response);
      setValidation(response.validation);
      if (editing) {
        // Editing an existing mapping starts from what that mapping says,
        // not from a fresh suggestion: the operator asked to change a
        // reviewed mapping, not to have it re-guessed.
        setDraft(draftFromSavedMapping(editing.name, editing.profile));
      } else if (response.proposal) {
        setDraft(draftFromProposal(response.proposal));
      } else {
        setDraft(EMPTY_DRAFT);
      }
    } catch (e) {
      setProposalResponse(null); setValidation(null); setDraft(EMPTY_DRAFT);
      setMappingError(e instanceof ApiError ? e.message : "Could not propose a mapping automatically. Map the columns below.");
    } finally { setProposing(false); }
  };

  const saveMapping = async () => {
    if (!bank) return;
    setSavingMapping(true); setMappingError(null);
    const body = new FormData();
    body.set("bank_file", bank);
    body.set("mapping", JSON.stringify(draftToRequest(draft, acknowledged, {
      signature: proposalResponse?.signature,
      llmProposal: proposalResponse?.proposal
        ? {
          provider: proposalResponse.proposal.provider,
          model: proposalResponse.proposal.model,
          reported_model: proposalResponse.proposal.reported_model,
          proposed_at: proposalResponse.proposal.proposed_at,
          sample_bounds: proposalResponse.sample.bounds,
        }
        : null,
    })));
    const path = editingMappingId ? `/api/bank-mappings/${encodeURIComponent(editingMappingId)}/versions` : "/api/bank-mappings";
    try {
      const result = await api<BankMappingSaveResponse>(path, { method: "POST", body });
      setSavedMapping(result.saved);
      setValidation(result.validation);
      setStage("saved");
    } catch (e) {
      if (e instanceof ApiError) {
        setMappingError(e.message);
        // The server returns its own validation alongside a rejection, so
        // the editor can point at the offending control rather than showing
        // a bare message.
        const rejected = e.detail.validation as MappingValidationView | undefined;
        if (rejected) setValidation(rejected);
      } else {
        setMappingError("The mapping could not be saved.");
      }
    } finally { setSavingMapping(false); }
  };

  const runDemo = async () => { setRunning(true); setError(null); setLiveUnavailable(false); setStatus("Loading demo fixtures and replay trajectories…"); try { const result = await api<RunResponse>("/api/reconciliation/demo", { method: "POST" }); complete(result); } catch (e) { setError(e instanceof ApiError ? e.message : "The demo batch failed."); } finally { setRunning(false); setStatus(""); } };

  const runUpload = async () => {
    if (!razorpay || !bank || !(detectedBuiltIn || activeSaved || profile)) return;
    setRunning(true); setError(null); setLiveUnavailable(false);
    setStatus(mode === "replay" ? "Running deterministic stages and replaying cached investigations…" : "Running deterministic stages; unresolved cases may contact configured providers…");
    const body = new FormData();
    body.set("razorpay_file", razorpay); body.set("bank_file", bank);
    // Exactly one profile source. The server re-verifies an id against
    // these bytes before ingesting anything, so this is a request, not an
    // authorization.
    if (activeSaved) body.set("saved_mapping_id", activeSaved.mapping_id);
    else if (detectedBuiltIn) body.set("built_in_profile_id", detectedBuiltIn.profile_id);
    else if (profile) body.set("bank_profile", profile);
    body.set("mode", mode);
    if (batchId.trim()) body.set("batch_id", batchId.trim());
    try { complete(await api<RunResponse>("/api/reconciliation/run", { method: "POST", body })); }
    catch (e) { setLiveUnavailable(e instanceof ApiError && e.code === "live_provider_not_configured"); setError(e instanceof ApiError ? e.message : "The reconciliation run failed."); }
    finally { setRunning(false); setStatus(""); }
  };

  const headers = proposalResponse?.raw_headers ?? inspection?.raw_headers ?? [];
  const dateFormats = proposalResponse?.supported_date_formats ?? [];

  return <div className="page run-page"><PageHeader eyebrow="New batch" title="Run reconciliation" description="Submit source data to the existing deterministic and bounded-investigation pipeline. Provider secrets stay on the server." />
    <div className="run-layout"><div className="run-main">
      <Card><div className="section-heading"><div><span className="step-label">01</span><h2>Source files</h2><p>Files are parsed by their existing adapters. Invalid source records are audited separately.</p></div></div>
        <div className="file-grid file-grid-two"><FileField label="Razorpay recon file" accept=".json,application/json" file={razorpay} onChange={setRazorpay} icon={<FileJson size={18} />} /><FileField label="Bank statement (CSV)" accept=".csv,text/csv" file={bank} onChange={chooseBank} icon={<FileSpreadsheet size={18} />} /></div>

        {inspecting && <div className="schema-panel" role="status"><Search size={15} /> <span>Inspecting bank statement schema…</span></div>}

        {!inspecting && detectedBuiltIn && inspection?.profile &&
          <div className="schema-panel schema-matched" role="status">
            <BadgeCheck size={16} />
            <div><strong>Bank format recognized</strong>
              <ProfileLine profile={inspection.profile} />
              <small>{TIER_LABEL[inspection.match_tier ?? ""] ?? "Schema match"} · no bank profile JSON needed</small>
            </div>
            <Button variant="quiet" onClick={() => setManualOverride(true)}>Change / use manual profile</Button>
          </div>}

        {/* A recognised saved mapping. No proposal is requested and no
            provider is contacted -- the mapping already exists and a human
            already confirmed it. */}
        {!inspecting && detectedSaved && stage !== "review" && !savedMapping &&
          <div className="schema-panel schema-matched" role="status">
            <UserCheck size={16} />
            <div><strong>Mapping recognized</strong>
              <div className="profile-line"><strong>{detectedSaved.name}</strong><small>version {detectedSaved.version} · human-confirmed · saved mapping</small></div>
              <small>{TIER_LABEL[inspection?.match_tier ?? ""] ?? "Schema match"} · no AI proposal needed</small>
            </div>
            <div className="schema-panel-actions">
              <Button variant="quiet" onClick={() => openMappingReview({ editing: detectedSaved })}><Pencil size={14} /> Change</Button>
              <Button variant="quiet" onClick={() => setManualOverride(true)}>Use manual profile</Button>
            </div>
          </div>}

        {!inspecting && inspection?.status === "ambiguous" &&
          <div className="schema-panel schema-ambiguous" role="alert">
            <AlertTriangle size={16} />
            <div><strong>Multiple known bank formats match this statement</strong>
              <small>FinRecon will not choose between them. Confirm a mapping for this file explicitly, or upload the bank profile JSON for the correct format below.</small>
              <ul className="schema-candidates">{ambiguousMatches().map((candidate) => <li key={candidate.profile_id}><MatchLine match={candidate} /></li>)}</ul>
            </div>
          </div>}

        {!inspecting && inspection?.status === "unknown" && stage === "idle" &&
          <div className="schema-panel schema-unknown" role="status">
            <AlertTriangle size={16} />
            <div><strong>We don't recognize this bank format</strong>
              <small>FinRecon does not guess column meanings. Let AI suggest a mapping for you to review, or declare the columns yourself — either way you confirm it before anything runs.</small>
              {inspection.raw_headers.length > 0 && <small className="schema-headers">Columns read: {inspection.raw_headers.join(" · ")}</small>}
            </div>
            <Button variant="secondary" onClick={() => openMappingReview()}><Sparkles size={15} /> Map these columns</Button>
          </div>}

        {stage === "review" && <div className="mapping-panel">
          <div className="section-heading"><div>
            <span className="step-label">01b</span>
            <h2>{editingMappingId ? "Edit saved mapping" : "Map this statement's columns"}</h2>
            <p>
              {editingMappingId
                ? "Saving creates a new version. The previous version stays exactly as it was, so batches that used it remain traceable."
                : "You review and correct every field. Nothing is saved, and no reconciliation can run, until you confirm."}
            </p>
          </div></div>

          {proposing && <div className="schema-panel" role="status"><Search size={15} /> <span>Asking the configured model for a suggested mapping…</span></div>}

          {!proposing && proposalResponse?.failure_code && <div className="schema-panel schema-unknown" role="status">
            <AlertTriangle size={16} />
            <div><strong>Could not propose a mapping automatically</strong>
              <small>{proposalResponse.failure_message ?? "The mapping-proposal service was unavailable."} Map the columns yourself below — everything still works.</small>
            </div>
          </div>}

          {!proposing && <>
            <SamplePreview headers={proposalResponse?.sample.headers ?? headers} rows={proposalResponse?.sample.rows ?? []} />
            <MappingEditor
              headers={headers}
              dateFormats={dateFormats}
              draft={draft}
              onChange={setDraft}
              validation={validation}
              proposal={proposalResponse?.proposal ?? null}
              acknowledged={acknowledged}
              onAcknowledge={(field, on) => setAcknowledged((current) => on ? [...new Set([...current, field])] : current.filter((item) => item !== field))}
              onSave={saveMapping}
              saving={savingMapping}
              saveLabel={editingMappingId ? "Save new version & continue" : "Save mapping & continue"}
              error={mappingError}
            />
            <Button variant="quiet" onClick={resetMapping}>Cancel</Button>
          </>}
        </div>}

        {stage === "saved" && savedMapping && <div className="schema-panel schema-matched" role="status">
          <UserCheck size={16} />
          <div><strong>Mapping saved — reconciliation can run</strong>
            <div className="profile-line"><strong>{savedMapping.name}</strong><small>version {savedMapping.version} · human-confirmed · reused automatically next time</small></div>
            <small>The server re-checks this statement against the saved mapping before reading a single row.</small>
          </div>
          <Button variant="quiet" onClick={() => openMappingReview({ editing: savedMapping })}><Pencil size={14} /> Edit mapping</Button>
        </div>}

        {needsManualProfile && stage === "idle" &&
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
      <aside><Card className="demo-card"><div className="demo-icon"><FlaskConical size={22} /></div><span className="eyebrow">Judge-ready path</span><h2>Load demo batch</h2><p>Runs real adapters, Stage 2, cached Stage 3 replay, validation, policy, and SQLite persistence.</p><ul><li><Check size={14} /> Rules-based matching</li><li><Check size={14} /> Evidence-assisted replay resolution</li><li><Check size={14} /> Safe escalation for human review</li><li><Check size={14} /> Separate ingestion quarantine</li></ul><Button variant="secondary" disabled={running} onClick={runDemo}>Load demo batch <ArrowRight size={15} /></Button><small>Zero network/provider calls</small></Card></aside>
    </div>
  </div>;
}
