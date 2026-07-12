"use client";

/**
 * Stale thread cleanup — DISABLED.
 *
 * Previously auto-archived threads whose checkpoints appeared missing
 * (no messages after 3 seconds). This caused mass thread loss during
 * server restarts — the cleanup couldn't distinguish "server is down"
 * from "checkpoint was deleted." Users can manually archive stale
 * threads via the sidebar. Re-enable once thread list syncs from the
 * backend /threads API instead of localStorage.
 */
export function useStaleThreadCleanup(
  _activeThreadId: string,
  _currentTitle: string,
  _archiveThread: (id: string) => void
) {
  // no-op
}
