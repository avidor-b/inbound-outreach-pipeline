"use client";

import { useState, useEffect, useCallback } from "react";
import dynamic from "next/dynamic";
import { CopilotKit } from "@copilotkit/react-core";
import { useAuth } from "@/hooks/use-auth";
import { useThreads } from "@/hooks/use-threads";
import { useAutoTitle } from "@/hooks/use-auto-title";
import { useOutreachUI } from "@/hooks/use-outreach-ui";
import { useSuggestions } from "@/hooks/use-suggestions";
import { useDraftApproval, useDeleteConfirmation } from "@/hooks/use-interrupts";
import { useStaleThreadCleanup } from "@/hooks/use-stale-thread-cleanup";
import { useThreadHydration } from "@/hooks/use-thread-hydration";
import { ThreadSidebar } from "@/components/thread-sidebar";
import { LoginForm } from "@/components/login-form";
import { ChatErrorBoundary } from "@/components/chat-error-boundary";

const CopilotChat = dynamic(
  () => import("@copilotkit/react-core/v2").then((m) => m.CopilotChat),
  { ssr: false }
);

/**
 * Track whether an interrupt (draft review, delete confirm) is pending.
 * While pending, the chat input shows a notice instead of accepting messages.
 */
function useInterruptPending() {
  const [pending, setPending] = useState(false);

  const onInterruptStart = useCallback(() => setPending(true), []);
  const onInterruptEnd = useCallback(() => setPending(false), []);

  return { interruptPending: pending, onInterruptStart, onInterruptEnd };
}

function ChatContent({
  activeThreadId,
  updateThreadTitle,
  archiveThread,
  currentTitle,
}: {
  activeThreadId: string;
  updateThreadTitle: (id: string, title: string) => void;
  archiveThread: (id: string) => void;
  currentTitle: string;
}) {
  const { interruptPending, onInterruptStart, onInterruptEnd } = useInterruptPending();

  useOutreachUI();
  useSuggestions();
  useDraftApproval(onInterruptStart, onInterruptEnd);
  useDeleteConfirmation(onInterruptStart, onInterruptEnd);
  useAutoTitle(activeThreadId, updateThreadTitle, currentTitle);
  useStaleThreadCleanup(activeThreadId, currentTitle, archiveThread);
  useThreadHydration(activeThreadId);

  return (
    <div className="relative h-full">
      <ChatErrorBoundary resetKey={activeThreadId}>
        <CopilotChat
          input={{
            disclaimer: () => null,
            className: "pb-4",
          }}
        />
      </ChatErrorBoundary>
      {interruptPending && (
        <div className="absolute bottom-0 left-0 right-0 z-50 h-36 pointer-events-auto flex items-center justify-center bg-white border-t border-gray-200">
          <span className="text-sm text-gray-700 font-semibold">
            Approve or reject the card above to continue
          </span>
        </div>
      )}
    </div>
  );
}

export default function HomePage() {
  const {
    user, loading, error, isAuthenticated,
    loginWithGoogle, loginWithEmail, signupWithEmail, logout,
  } = useAuth();
  const {
    threads, activeThreadId, createThread, selectThread,
    archiveThread, updateThreadTitle, touchThread,
  } = useThreads(user?.id ?? "");

  if (loading) return null;

  if (!isAuthenticated) {
    return (
      <LoginForm
        onGoogleLogin={loginWithGoogle}
        onEmailLogin={loginWithEmail}
        onEmailSignup={signupWithEmail}
        error={error}
      />
    );
  }

  const activeThread = threads.find((t) => t.id === activeThreadId);
  if (!activeThreadId || !activeThread) return null;

  return (
    <CopilotKit
      key={activeThreadId}
      runtimeUrl="/api/copilotkit"
      threadId={activeThreadId}
      credentials="include"
    >
      <div className="flex h-screen w-full">
        <ThreadSidebar
          threads={threads}
          activeThreadId={activeThreadId}
          onSelectThread={selectThread}
          onNewThread={createThread}
          onArchiveThread={archiveThread}
        />

        <main className="flex-1 flex flex-col min-w-0">
          <header className="flex items-center justify-between px-6 py-3 border-b border-border/20">
            <span className="font-[family-name:var(--font-headline)] text-lg font-bold tracking-tight">
              {activeThread?.title || "New Conversation"}
            </span>
            <div className="flex items-center gap-3">
              <span className="text-sm text-text-muted">{user?.name}</span>
              <button
                onClick={logout}
                className="text-sm text-text-faint hover:text-text-muted transition-colors"
              >
                Sign out
              </button>
              <span className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-primary flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-primary rounded-full" />
                Agent Active
              </span>
            </div>
          </header>
          <div className="flex-1 overflow-hidden">
            <ChatContent
              activeThreadId={activeThreadId}
              updateThreadTitle={updateThreadTitle}
              archiveThread={archiveThread}
              currentTitle={activeThread?.title || ""}
            />
          </div>
        </main>
      </div>
    </CopilotKit>
  );
}
