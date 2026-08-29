import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import { LoadingState } from "./components/ui";

const CaseDetail = lazy(() => import("./pages/CaseDetail"));
const Issues = lazy(() => import("./pages/Issues"));
const Overview = lazy(() => import("./pages/Overview"));
const Queue = lazy(() => import("./pages/Queue"));
const Run = lazy(() => import("./pages/Run"));

export default function App() {
  return <Suspense fallback={<LoadingState label="Loading FinRecon workspace" />}><Routes><Route element={<Shell />}><Route index element={<Overview />} /><Route path="cases" element={<Queue />} /><Route path="cases/:caseId" element={<CaseDetail />} /><Route path="issues" element={<Issues />} /><Route path="run" element={<Run />} /><Route path="*" element={<Navigate to="/" replace />} /></Route></Routes></Suspense>;
}
