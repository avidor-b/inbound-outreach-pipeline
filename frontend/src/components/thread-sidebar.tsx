"use client";

import { useCallback } from "react";

export interface Thread {
  id: string;
  title: string;
  updatedAt: string;
  status: "active" | "archived";
}

interface ThreadSidebarProps {
  threads: Thread[];
  activeThreadId: string;
  onSelectThread: (id: string) => void;
  onNewThread: () => void;
  onArchiveThread: (id: string) => void;
}

export function ThreadSidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onNewThread,
  onArchiveThread,
}: ThreadSidebarProps) {
  const activeThreads = threads
    .filter((t) => t.status === "active")
    // Always sort on render: useThreads only sorts on initial load;
    // subsequent touches (e.g. new messages) update `updatedAt` in the
    // underlying array but don't re-sort it, so without this the
    // sidebar stays in whatever order the array happened to be in.
    .slice()
    .sort((a, b) => (b.updatedAt || "").localeCompare(a.updatedAt || ""));

  const formatTime = useCallback((iso: string) => {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHrs = Math.floor(diffMin / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    const diffDays = Math.floor(diffHrs / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  }, []);

  return (
    <aside className="hidden md:flex flex-col h-full w-60 border-r border-border/20 bg-surface py-6 px-4">
      {/* Logo */}
      <div className="mb-6 px-2 flex items-center gap-3">
        <div className="w-8 h-8 bg-primary rounded-sm flex items-center justify-center">
          <span className="text-white text-sm font-bold">O</span>
        </div>
        <div>
          <h1 className="font-[family-name:var(--font-headline)] font-black text-sm tracking-tight">
            Outreach
          </h1>
          <p className="text-sm text-text-muted">Outreach Pipeline</p>
        </div>
      </div>

      {/* New Thread button */}
      <button
        onClick={onNewThread}
        className="mx-2 mb-4 flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium
                   bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        New Thread
      </button>

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto">
        <p className="font-[family-name:var(--font-label)] uppercase tracking-widest text-sm text-text-muted mb-2 px-2">
          Threads
        </p>
        {activeThreads.length === 0 && (
          <p className="text-text-faint text-sm italic px-2">No threads yet</p>
        )}
        <ul className="space-y-0.5">
          {activeThreads.map((thread) => (
            <li key={thread.id}>
              <div
                role="button"
                data-thread-id={thread.id}
                tabIndex={0}
                onClick={() => onSelectThread(thread.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelectThread(thread.id)}
                className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors group cursor-pointer
                  ${
                    thread.id === activeThreadId
                      ? "bg-primary/10 text-primary font-medium"
                      : "text-text-muted hover:bg-surface-high"
                  }`}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate flex-1">
                    {thread.title || "New conversation"}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onArchiveThread(thread.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 ml-1 p-0.5 text-text-faint hover:text-text-muted transition-opacity"
                    title="Archive"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="3,6 5,6 21,6" />
                      <path d="M19,6v14a2,2,0,0,1-2,2H7a2,2,0,0,1-2-2V6m3,0V4a2,2,0,0,1,2-2h4a2,2,0,0,1,2,2v2" />
                    </svg>
                  </button>
                </div>
                <span className="text-sm text-text-faint">
                  {formatTime(thread.updatedAt)}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
