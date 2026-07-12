"use client";

import { useEffect, useRef } from "react";
import { useAgent } from "@copilotkit/react-core/v2";

/**
 * Auto-titles a thread from the first user message.
 *
 * Uses useAgent().agent.messages (AG-UI Messages) to detect when a user
 * message appears. Works for both typed input and suggestion clicks.
 */
export function useAutoTitle(
  activeThreadId: string,
  updateThreadTitle: (id: string, title: string) => void,
  currentTitle: string
) {
  const { agent } = useAgent();
  const titled = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!activeThreadId || titled.current.has(activeThreadId) || currentTitle) {
      if (currentTitle) titled.current.add(activeThreadId);
      return;
    }

    const msgs = agent?.messages;
    if (!msgs || msgs.length === 0) return;

    const firstUser = msgs.find(
      (m: any) => m.role === "user" && m.content
    );
    if (!firstUser) return;

    const text = String((firstUser as any).content).trim();
    if (!text) return;

    const title = text.length > 40 ? text.slice(0, 37) + "..." : text;
    updateThreadTitle(activeThreadId, title);
    titled.current.add(activeThreadId);
  }, [agent?.messages, activeThreadId, currentTitle, updateThreadTitle]);
}
