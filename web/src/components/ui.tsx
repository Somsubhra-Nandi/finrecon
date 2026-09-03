import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";
import { AlertCircle, LoaderCircle } from "lucide-react";
import type { ResolutionSource } from "../types";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx("card", className)} {...props} />;
}

export function Button({ className, variant = "primary", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" | "danger" }) {
  return <button className={clsx("button", `button-${variant}`, className)} {...props} />;
}

const labels: Record<ResolutionSource, string> = {
  deterministic: "Deterministic", ai_assisted: "Evidence-assisted", human: "Human resolved", escalated: "Requires human review",
};
export function StatusBadge({ source }: { source: ResolutionSource }) {
  return <span className={clsx("status-badge", `status-${source}`)}><span aria-hidden="true" />{labels[source]}</span>;
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <Card className="empty-state"><div className="empty-icon">{icon ?? <AlertCircle size={20} />}</div><h2>{title}</h2><p>{children}</p>{action}</Card>;
}

export function LoadingState({ label = "Loading reconciliation data" }: { label?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle className="spin" size={20} /><span>{label}</span></div>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return <Card className="error-state" role="alert"><AlertCircle size={20} /><div><h2>We couldn’t load this view</h2><p>{error.message}</p>{retry && <Button variant="secondary" onClick={retry}>Try again</Button>}</div></Card>;
}

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1><p>{description}</p></div>{action && <div className="page-action">{action}</div>}</header>;
}

export function JsonDetails({ label, value }: { label: string; value: unknown }) {
  return <details className="json-details"><summary>{label}</summary><pre className="technical-payload">{JSON.stringify(value, null, 2)}</pre></details>;
}
