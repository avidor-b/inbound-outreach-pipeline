"use client";

import React from "react";

/**
 * Catches the "Agent not found" error that CopilotKit's
 * `useRenderCustomMessages` hook throws during the window between
 * a thread-switch remount and the agent registry being rehydrated
 * from /api/copilotkit/info.
 *
 * Source of the throw:
 *   @copilotkitnext/react/dist/hooks/use-render-custom-messages.mjs:23
 *   if (!agent) throw new Error("Agent not found");
 *
 * Recovery strategy:
 *   - Catch the specific error in getDerivedStateFromError.
 *   - Schedule a retry with a small backoff (150ms + 100ms per prior
 *     attempt). Most thread switches have the agent registry back in
 *     one tick; a few dev-mode double-renders need 2-3 attempts.
 *   - Bound at 5 attempts; past that we render a manual "Retry"
 *     fallback so the user isn't stuck on a blank screen.
 *   - `resetKey` prop resets attempts on thread switch, so a stuck
 *     thread doesn't block a fresh one.
 *
 * Unknown errors are re-thrown so genuine bugs still surface.
 */

const MAX_ATTEMPTS = 5;
const BASE_DELAY_MS = 150;

interface State {
  error: Error | null;
  attempt: number;
}

interface Props {
  children: React.ReactNode;
  /** Changes when the user switches threads — resets the retry counter. */
  resetKey?: string;
}

function isAgentNotFound(err: unknown): boolean {
  if (!err) return false;
  const msg = err instanceof Error ? err.message : String(err);
  return msg === "Agent not found" || msg.startsWith("Agent not found");
}

export class ChatErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, attempt: 0 };
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(error: Error): Partial<State> | null {
    if (isAgentNotFound(error)) return { error };
    // Unknown error — let React's default handling take over.
    return null;
  }

  componentDidCatch(error: Error) {
    if (!isAgentNotFound(error)) throw error;
    if (this.state.attempt >= MAX_ATTEMPTS) return;
    const delay = BASE_DELAY_MS + this.state.attempt * 100;
    this.retryTimer = setTimeout(() => {
      this.setState((s) => ({ error: null, attempt: s.attempt + 1 }));
    }, delay);
  }

  componentDidUpdate(prev: Props) {
    // New thread — reset so a fresh boundary attempt starts clean.
    if (prev.resetKey !== this.props.resetKey) {
      if (this.retryTimer) clearTimeout(this.retryTimer);
      this.retryTimer = null;
      if (this.state.error || this.state.attempt) {
        this.setState({ error: null, attempt: 0 });
      }
    }
  }

  componentWillUnmount() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
  }

  handleManualRetry = () => {
    this.setState({ error: null, attempt: 0 });
  };

  render() {
    if (this.state.error && this.state.attempt >= MAX_ATTEMPTS) {
      return (
        <div className="flex flex-col items-center justify-center h-full text-sm text-text-muted gap-2">
          <p>The chat is warming up — one more moment.</p>
          <button
            onClick={this.handleManualRetry}
            className="px-3 py-1 rounded border border-border/30 hover:border-primary hover:text-primary"
          >
            Retry
          </button>
        </div>
      );
    }
    if (this.state.error) {
      // Mid-retry — render nothing so we don't flash partial UI.
      return null;
    }
    return this.props.children;
  }
}
