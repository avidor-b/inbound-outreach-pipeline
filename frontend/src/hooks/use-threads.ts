"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import type { Thread } from "@/components/thread-sidebar";

const LEGACY_KEY = "outreach-threads"; // pre-user-scoping
const STORAGE_PREFIX = "outreach-threads:";
const MAX_THREADS = 200;
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

const storageKey = (userId: string) =>
  userId ? `${STORAGE_PREFIX}${userId}` : LEGACY_KEY;

function generateId(): string {
  return crypto.randomUUID();
}

function loadThreads(userId: string): Thread[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(storageKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (t: any) => t && typeof t.id === "string" && typeof t.status === "string"
    );
  } catch (e) {
    console.warn("Failed to load threads from localStorage:", e);
    return [];
  }
}

function saveThreads(userId: string, threads: Thread[]) {
  if (typeof window === "undefined") return;
  try {
    const toSave = threads.slice(0, MAX_THREADS);
    localStorage.setItem(storageKey(userId), JSON.stringify(toSave));
  } catch (e) {
    console.warn("Failed to save threads to localStorage:", e);
  }
}

function makeThread(id?: string): Thread {
  return {
    id: id || generateId(),
    title: "",
    updatedAt: new Date().toISOString(),
    status: "active",
  };
}

/**
 * Manages thread list and active thread selection.
 *
 * Thread metadata (title, timestamp) is stored in localStorage, keyed
 * by user ID so multiple accounts on the same browser don't see each
 * other's threads (the backend also enforces ownership on /copilotkit).
 * The actual conversation state lives in the LangGraph checkpointer.
 */
export function useThreads(userId: string = "") {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string>("");
  const lastUserId = useRef<string>("");

  // (Re)load whenever the signed-in user changes. We DROP any
  // previously-held in-memory state so a quick sign-out/sign-in on
  // the same browser can never surface user A's thread list to user B.
  useEffect(() => {
    if (!userId) {
      setThreads([]);
      setActiveThreadId("");
      lastUserId.current = "";
      return;
    }
    if (lastUserId.current === userId) return;
    lastUserId.current = userId;

    // One-time legacy → user-scoped migration (pre-user-scoping data).
    if (typeof window !== "undefined") {
      try {
        const scopedRaw = localStorage.getItem(storageKey(userId));
        const legacyRaw = localStorage.getItem(LEGACY_KEY);
        if (!scopedRaw && legacyRaw) {
          localStorage.setItem(storageKey(userId), legacyRaw);
        }
        localStorage.removeItem(LEGACY_KEY);
      } catch { /* ignore */ }
    }

    // Show local data immediately for a snappy boot, then reconcile
    // with the backend. The backend is authoritative (DB has every
    // thread the user has ever used, with titles backfilled), but
    // hitting it synchronously on mount would delay the sidebar.
    const stored = loadThreads(userId);
    const storedActive = stored.filter((t) => t.status === "active");
    if (storedActive.length > 0) {
      storedActive.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
      setThreads(stored);
      setActiveThreadId(storedActive[0].id);
    }

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/threads`, {
          credentials: "include",
        });
        if (!res.ok) throw new Error(`/threads HTTP ${res.status}`);
        const rows = await res.json();
        if (cancelled) return;

        const hydrated: Thread[] = Array.isArray(rows)
          ? rows
              .filter((r: any) => r && r.id)
              .map((r: any) => ({
                id: String(r.id),
                title: String(r.title || ""),
                updatedAt:
                  r.updated_at || r.created_at || new Date().toISOString(),
                status: (r.status === "archived" ? "archived" : "active") as
                  | "archived"
                  | "active",
              }))
          : [];

        // Merge: server threads win on title / status (authoritative
        // persisted state), but any purely-local thread not yet
        // claimed on the server (brand new, no /copilotkit call made
        // yet) is preserved.
        const serverIds = new Set(hydrated.map((t) => t.id));
        const localOnly = stored.filter((t) => !serverIds.has(t.id));
        const merged = [...hydrated, ...localOnly];
        const activeMerged = merged.filter((t) => t.status === "active");

        if (activeMerged.length > 0) {
          activeMerged.sort((a, b) =>
            b.updatedAt.localeCompare(a.updatedAt),
          );
          setThreads(merged);
          setActiveThreadId((cur) => cur || activeMerged[0].id);
          saveThreads(userId, merged);
        } else {
          // No threads anywhere — start fresh.
          const fresh = makeThread();
          setThreads([fresh]);
          setActiveThreadId(fresh.id);
          saveThreads(userId, [fresh]);
        }
      } catch {
        if (cancelled) return;
        if (storedActive.length === 0) {
          const fresh = makeThread();
          setThreads([fresh]);
          setActiveThreadId(fresh.id);
          saveThreads(userId, [fresh]);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [userId]);

  // Persist whenever threads change
  useEffect(() => {
    if (userId && threads.length > 0) {
      saveThreads(userId, threads);
    }
  }, [threads, userId]);

  const createThread = useCallback(() => {
    const thread = makeThread();
    setThreads((prev) => [thread, ...prev]);
    setActiveThreadId(thread.id);
    return thread.id;
  }, []);

  const selectThread = useCallback((id: string) => {
    setActiveThreadId(id);
  }, []);

  /**
   * Archive a thread. If it's the active thread, switch to another
   * or create a fresh one. Uses a ref to read the latest threads
   * without a stale closure.
   */
  const threadsRef = useRef(threads);
  threadsRef.current = threads;
  const activeRef = useRef(activeThreadId);
  activeRef.current = activeThreadId;

  const archiveThread = useCallback((id: string) => {
    const current = threadsRef.current;
    const isActive = id === activeRef.current;

    // Build updated list
    const updated = current.map((t) =>
      t.id === id ? { ...t, status: "archived" as const } : t
    );

    if (isActive) {
      const remaining = updated.filter(
        (t) => t.status === "active" && t.id !== id
      );
      if (remaining.length > 0) {
        setThreads(updated);
        setActiveThreadId(remaining[0].id);
      } else {
        const fresh = makeThread();
        setThreads([fresh, ...updated]);
        setActiveThreadId(fresh.id);
      }
    } else {
      setThreads(updated);
    }

    // Persist archive to the backend. Without this, a hard reload
    // re-hydrates the thread from /threads (which only returns
    // status=active rows by default, but the merge logic had server
    // status winning over localStorage) — net effect: archived
    // threads reappeared after Ctrl-Shift-R.
    fetch(`${BACKEND_URL}/threads/${encodeURIComponent(id)}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "archived" }),
    }).catch(() => {});
  }, []);

  const updateThreadTitle = useCallback((id: string, title: string) => {
    // Only the title changes here. We intentionally DO NOT bump
    // `updatedAt`: useAutoTitle fires whenever a stored thread has an
    // empty local title (e.g. on first click after a localStorage
    // restore from backend), and bumping the timestamp there made the
    // thread jump to "just now" on every click. updatedAt is driven
    // by real activity (message send) elsewhere.
    setThreads((prev) =>
      prev.map((t) => (t.id === id ? { ...t, title } : t))
    );
    // Persist to the backend so a localStorage wipe doesn't lose it.
    fetch(`${BACKEND_URL}/threads/${encodeURIComponent(id)}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).catch(() => {});
  }, []);

  const touchThread = useCallback((id: string) => {
    setThreads((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, updatedAt: new Date().toISOString() } : t
      )
    );
  }, []);

  return {
    threads,
    activeThreadId,
    createThread,
    selectThread,
    archiveThread,
    updateThreadTitle,
    touchThread,
  };
}
