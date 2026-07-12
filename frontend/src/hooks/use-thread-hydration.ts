"use client";

import { useEffect, useRef } from "react";
import { useAgent } from "@copilotkit/react-core/v2";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

/**
 * Seeds the agent's message list from the thread's server-side
 * LangGraph checkpoint on first mount per thread.
 *
 * CopilotKit v2's HttpAgent does not implement `connect`, so clicking
 * an existing thread would otherwise show an empty chat until the
 * user sends a message (which only reveals the new turn, not prior
 * history). This hook fetches /threads/{id}/history and calls
 * agent.setMessages so the full transcript renders.
 *
 * Race we explicitly handle: on mount, CopilotChat calls
 * CopilotKitCore.connectAgent(), which internally runs
 * `agent.setMessages([])` mid-connect. If our hydration fetch resolves
 * *before* that wipe, our messages get blown away and the chat looks
 * empty. We therefore (a) track hydration by the (threadId, agent)
 * pair since useAgent can hand out a provisional agent first and swap
 * in the real one once the runtime reports ready, and (b) retry
 * `setMessages` on a short polling loop until it sticks — long enough
 * to outlast the worst observed connect-clobber window.
 *
 * Only hydrates when agent.messages is empty — once the user starts
 * interacting, we never overwrite what they see.
 */
export function useThreadHydration(threadId: string) {
  const { agent } = useAgent();
  const hydrated = useRef<{ threadId: string; agent: object } | null>(null);

  useEffect(() => {
    if (!threadId || !agent) return;
    if (
      hydrated.current?.threadId === threadId &&
      hydrated.current?.agent === agent
    ) {
      return;
    }
    if ((agent.messages?.length ?? 0) > 0) {
      hydrated.current = { threadId, agent };
      return;
    }

    let cancelled = false;
    let pendingMsgs: unknown[] | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    // 20 × 200ms = 4s window, enough to survive the longest
    // connect-clobber we've observed in testing.
    const MAX_ATTEMPTS = 20;
    const RETRY_MS = 200;

    const tryApply = () => {
      if (cancelled || !pendingMsgs) return;
      if ((agent.messages?.length ?? 0) > 0) {
        hydrated.current = { threadId, agent };
        return;
      }
      agent.setMessages(pendingMsgs as Parameters<typeof agent.setMessages>[0]);
      attempts++;
      if (attempts < MAX_ATTEMPTS) {
        retryTimer = setTimeout(tryApply, RETRY_MS);
      } else {
        hydrated.current = { threadId, agent };
      }
    };

    (async () => {
      try {
        const res = await fetch(
          `${BACKEND_URL}/threads/${encodeURIComponent(threadId)}/history`,
          { credentials: "include" },
        );
        if (!res.ok) return;
        const msgs = await res.json();
        if (cancelled) return;
        if (Array.isArray(msgs) && msgs.length > 0) {
          pendingMsgs = msgs;
          tryApply();
        }
      } catch {
        // silent — an empty chat is the safe fallback
      }
    })();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [threadId, agent]);
}
