import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Catches failures from the lazy route imports in App.
 *
 * Every route is a dynamically imported chunk whose filename carries a content
 * hash, so a browser running a stale index.html requests chunk names the
 * current deploy no longer serves. Without a boundary that rejection unmounts
 * the tree and the operator gets a blank white page with nothing to act on.
 * Reloading fetches a fresh index.html and resolves it, so that is what this
 * offers.
 */
export default class ChunkBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("FinRecon failed to load a route chunk", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="chunk-boundary" role="alert">
      <strong>This page could not finish loading.</strong>
      <p>FinRecon was updated while this tab was open, so part of the console is out of date. Reloading fetches the current version.</p>
      <button type="button" onClick={() => window.location.reload()}>Reload FinRecon</button>
    </div>;
  }
}
