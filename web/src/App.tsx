import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import ChunkBoundary from "./components/ChunkBoundary";
import Shell from "./components/Shell";
import { LoadingState } from "./components/ui";

const CaseDetail = lazy(() => import("./pages/CaseDetail"));
const Issues = lazy(() => import("./pages/Issues"));
const Overview = lazy(() => import("./pages/Overview"));
const Queue = lazy(() => import("./pages/Queue"));
const Run = lazy(() => import("./pages/Run"));
const Benchmarks = lazy(() => import("./pages/Benchmarks"));
const Landing = lazy(() => import("./pages/Landing"));

function LegacyQueueRedirect() {
  const location = useLocation();
  return <Navigate to={`/reconciliation${location.search}`} replace />;
}

function LegacyCaseRedirect() {
  const { caseId = "" } = useParams();
  const location = useLocation();
  return <Navigate to={`/reconciliation/${encodeURIComponent(caseId)}${location.search}`} replace />;
}

export default function App() {
  return <ChunkBoundary><Suspense fallback={<LoadingState label="Loading FinRecon workspace" />}><Routes>
    <Route path="/" element={<Landing />} />
    <Route element={<Shell />}>
      <Route path="overview" element={<Overview />} />
      <Route path="reconciliation" element={<Queue />} />
      <Route path="reconciliation/:caseId" element={<CaseDetail />} />
      <Route path="issues" element={<Issues />} />
      <Route path="source-issues" element={<Issues />} />
      <Route path="run" element={<Run />} />
      <Route path="benchmarks/*" element={<Benchmarks />} />
    </Route>
    <Route path="cases" element={<LegacyQueueRedirect />} />
    <Route path="cases/:caseId" element={<LegacyCaseRedirect />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></Suspense></ChunkBoundary>;
}
