"use client";

import { useState, useRef } from "react";
import { useLangGraphInterrupt } from "@copilotkit/react-core";

/** Catalog entry surfaced on the Draft Review card. Mirrors the dict
 *  returned by find_sendable_asset on the backend. */
type PromisedResource = {
  id: string;
  title: string;
  url: string;
  asset_type: string;
  summary?: string;
  match_score?: number;
};

function DraftActions({ resolve, engagementId }: { resolve: (v: string) => void; engagementId?: number }) {
  const [status, setStatus] = useState<"pending" | "approved" | "discarded" | "revising" | "forwarded">("pending");

  if (status === "approved") {
    return (
      <div className="px-4 py-3 border-t border-emerald-500/20 bg-emerald-50/5">
        <span className="text-sm font-semibold text-emerald-600">
          Draft approved and saved{engagementId ? ` for engagement #${engagementId}` : ""}.
        </span>
      </div>
    );
  }

  if (status === "forwarded") {
    return (
      <div className="px-4 py-3 border-t border-blue-500/20 bg-blue-50/5">
        <span className="text-sm font-semibold text-blue-600">
          Forwarded to SDR{engagementId ? ` — engagement #${engagementId} flagged for human follow-up.` : "."}
        </span>
      </div>
    );
  }

  if (status === "discarded") {
    return (
      <div className="px-4 py-3 border-t border-border/10">
        <span className="text-sm font-semibold text-text-muted">Draft discarded.</span>
      </div>
    );
  }

  if (status === "revising") {
    return (
      <div className="px-4 py-3 border-t border-yellow-500/20 bg-yellow-50/5">
        <span className="text-sm font-semibold text-yellow-600">Revising draft...</span>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 border-t border-border/10 space-y-2">
      <div className="flex gap-2 flex-wrap">
        <button
          onClick={() => {
            setStatus("approved");
            resolve(JSON.stringify({ approved: true }));
          }}
          className="px-4 py-1.5 rounded text-sm font-semibold bg-primary text-white hover:opacity-90 transition-opacity"
        >
          Approve & Save
        </button>
        <button
          onClick={() => {
            setStatus("forwarded");
            resolve(JSON.stringify({ approved: false, forward_sdr: true }));
          }}
          className="px-4 py-1.5 rounded text-sm font-semibold border border-blue-500/40 text-blue-600 hover:bg-blue-50/20 transition-colors"
          title="Flag this engagement for an SDR to handle manually — do not send"
        >
          Forward to SDR
        </button>
        <button
          onClick={() => {
            setStatus("discarded");
            resolve(JSON.stringify({ approved: false }));
          }}
          className="px-4 py-1.5 rounded text-sm font-semibold text-text-faint hover:text-text-muted transition-colors"
        >
          Discard
        </button>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const feedback = new FormData(e.currentTarget).get("feedback") as string;
          if (feedback?.trim()) {
            setStatus("revising");
            resolve(JSON.stringify({ approved: false, feedback: feedback.trim() }));
          }
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          name="feedback"
          placeholder="Revision feedback..."
          className="flex-1 text-sm rounded border border-border/40 bg-background px-2 py-1.5"
        />
        <button
          type="submit"
          className="px-3 py-1.5 rounded text-sm font-semibold border border-border/40 text-text-muted hover:bg-surface-hover transition-colors"
        >
          Revise
        </button>
      </form>
    </div>
  );
}

function DeleteActions({ resolve, company }: { resolve: (v: string) => void; company: string }) {
  const [status, setStatus] = useState<"pending" | "deleted" | "cancelled">("pending");

  if (status === "deleted") {
    return (
      <div className="px-4 py-3 border-t border-red-500/10">
        <span className="text-sm font-semibold text-red-500">
          {company} deleted permanently.
        </span>
      </div>
    );
  }

  if (status === "cancelled") {
    return (
      <div className="px-4 py-3 border-t border-border/10">
        <span className="text-sm font-semibold text-text-muted">Delete cancelled.</span>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 border-t border-red-500/10 flex gap-2">
      <button
        onClick={() => {
          setStatus("deleted");
          resolve(JSON.stringify({ confirmed: true }));
        }}
        className="px-4 py-1.5 rounded text-sm font-semibold bg-red-600 text-white hover:bg-red-700 transition-colors"
      >
        Delete Permanently
      </button>
      <button
        onClick={() => {
          setStatus("cancelled");
          resolve(JSON.stringify({ confirmed: false }));
        }}
        className="px-4 py-1.5 rounded text-sm font-semibold border border-border/40 text-text-muted hover:bg-surface-hover transition-colors"
      >
        Cancel
      </button>
    </div>
  );
}

/**
 * Draft approval interrupt — renders inside CopilotChat with full context.
 */
export function useDraftApproval(
  onPending?: () => void,
  onResolved?: () => void,
) {
  // Track pending state via a ref + effect to avoid side effects in render
  const isPendingRef = useRef(false);
  const onPendingRef = useRef(onPending);
  const onResolvedRef = useRef(onResolved);
  onPendingRef.current = onPending;
  onResolvedRef.current = onResolved;

  useLangGraphInterrupt({
    enabled: ({ eventValue }) => {
      const v = eventValue?.action ?? eventValue?.args?.action;
      if (v === "approve_draft" && !isPendingRef.current) {
        isPendingRef.current = true;
        // Schedule state update outside render
        setTimeout(() => onPendingRef.current?.(), 0);
      }
      return v === "approve_draft";
    },
    render: ({ event, resolve: rawResolve }) => {
      const resolve = (v: string) => {
        isPendingRef.current = false;
        setTimeout(() => onResolvedRef.current?.(), 0);
        rawResolve(v);
      };
      const val = event.value ?? {};
      const args = val.args ?? val;
      const {
        subject,
        body,
        engagement_id,
        company,
        score,
        score_reasoning,
        primary_product,
        company_context,
        contact_message,
        intent_type,
        industry,
        region,
      } = args;
      const visitedPages: { url: string; title?: string }[] = Array.isArray(
        args.visited_pages,
      )
        ? args.visited_pages
        : [];

      // Sendable-resource metadata. Defaults guard against older drafts
      // saved before the catalog feature shipped (fields will be missing
      // from the interrupt payload).
      const promisedResources: PromisedResource[] = Array.isArray(
        args.promised_resources,
      )
        ? args.promised_resources
        : [];
      const referencedIds: string[] = Array.isArray(
        args.referenced_resource_ids,
      )
        ? args.referenced_resource_ids
        : [];
      const unverifiedPromises: string[] = Array.isArray(
        args.unverified_promises,
      )
        ? args.unverified_promises
        : [];
      const referencedSet = new Set(referencedIds);

      return (
        <div className="my-3 rounded-md border border-border/40 bg-surface overflow-hidden">
          {/* Header */}
          <div className="px-4 py-2.5 bg-primary/5 border-b border-border/20 flex items-center justify-between">
            <span className="text-sm font-semibold uppercase tracking-wider text-primary">
              Draft Review
            </span>
            <span className="text-sm font-mono text-text-muted">
              Engagement #{engagement_id}
            </span>
          </div>

          {/* Unverified-promise warning — surfaces phrases the drafter
              committed to send that don't match the catalog. Warns; does
              not block approval. */}
          {unverifiedPromises.length > 0 && (
            <div className="px-4 py-2.5 border-b border-yellow-500/30 bg-yellow-50/10">
              <div className="flex items-start gap-2">
                <span className="text-yellow-600 text-sm leading-tight">⚠</span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-yellow-700">
                    {unverifiedPromises.length === 1
                      ? "This draft promises a resource not in the catalog"
                      : `This draft promises ${unverifiedPromises.length} resources not in the catalog`}
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {unverifiedPromises.map((phrase, i) => (
                      <li
                        key={i}
                        className="text-sm text-yellow-700/80 italic truncate"
                      >
                        &ldquo;{phrase}&rdquo;
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-sm text-yellow-700/60">
                    Verify the resource exists before approving, or revise
                    the draft to cite an entry from the panel below.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Context summary */}
          <div className="px-4 py-3 border-b border-border/10 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {company && (
              <div>
                <span className="text-text-faint">Company</span>
                <p className="font-medium truncate">{company}</p>
                {(industry || region) && (
                  <p className="text-sm text-text-muted">
                    {[industry, region].filter(Boolean).join(" · ")}
                  </p>
                )}
              </div>
            )}
            {score > 0 && (
              <div>
                <span className="text-text-faint">Score</span>
                <p className="font-medium">
                  {score}/100
                  {primary_product && (
                    <span className="ml-1.5 text-text-muted">({primary_product})</span>
                  )}
                </p>
              </div>
            )}
            {score_reasoning && (
              <div className="col-span-2">
                <span className="text-text-faint">Reasoning</span>
                <p className="text-text-muted">{score_reasoning}</p>
              </div>
            )}
            {contact_message && (
              <div className="col-span-2">
                <span className="font-semibold text-text">
                  Contact said{intent_type ? ` (${intent_type})` : ""}
                </span>
                <blockquote className="mt-1 border-l-4 border-primary/40 bg-primary/5 pl-3 pr-3 py-2 text-text-muted italic whitespace-pre-wrap">
                  {contact_message}
                </blockquote>
              </div>
            )}
            {company_context && (
              <div className="col-span-2">
                <span className="text-text-faint">Company context</span>
                <p className="text-text-muted">{company_context}</p>
              </div>
            )}
            {visitedPages.length > 0 && (
              <div className="col-span-2">
                <span className="text-text-faint">Pages visited</span>
                <ul className="mt-1 space-y-0.5">
                  {visitedPages.map((p) => {
                    let pretty = p.url;
                    try {
                      const u = new URL(p.url);
                      const segs = u.pathname
                        .replace(/\/$/, "")
                        .split("/")
                        .filter(Boolean);
                      pretty = (segs[segs.length - 1] || u.hostname).replace(
                        /-/g,
                        " ",
                      );
                    } catch {
                      /* keep raw */
                    }
                    return (
                      <li key={p.url} className="text-sm">
                        <a
                          href={p.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline"
                          title={p.title || p.url}
                        >
                          {pretty}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </div>

          {/* Sendable resources retrieved for this draft. Cards with a
              "Cited" badge are URLs that appear in the email body. */}
          {promisedResources.length > 0 && (
            <div className="px-4 py-3 border-b border-border/10">
              <p className="text-sm text-text-faint mb-2">
                Resources retrieved for this draft
              </p>
              <ul className="space-y-1.5">
                {promisedResources.slice(0, 3).map((r) => {
                  const isReferenced = referencedSet.has(r.id);
                  return (
                    <li
                      key={r.id}
                      className={`rounded border px-3 py-2 ${
                        isReferenced
                          ? "border-emerald-500/30 bg-emerald-50/5"
                          : "border-border/20 bg-background"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm font-medium text-primary hover:underline truncate flex-1 min-w-0"
                        >
                          {r.title}
                        </a>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase tracking-wider bg-border/30 text-text-muted">
                            {r.asset_type}
                          </span>
                          {isReferenced && (
                            <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-700">
                              ✓ Cited
                            </span>
                          )}
                        </div>
                      </div>
                      {r.summary && (
                        <p className="mt-1 text-sm text-text-muted leading-snug line-clamp-2">
                          {r.summary}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Draft */}
          <div className="px-4 py-3">
            <p className="text-sm text-text-faint mb-1">Subject</p>
            <p className="text-sm font-medium mb-3">{subject}</p>
            <pre className="whitespace-pre-wrap text-sm text-text-muted bg-background rounded p-3 border border-border/20">
              {body}
            </pre>
          </div>

          {/* Actions */}
          <DraftActions resolve={resolve} engagementId={engagement_id} />
        </div>
      );
    },
  });
}

/**
 * Delete confirmation interrupt — renders inside CopilotChat.
 */
export function useDeleteConfirmation(
  onPending?: () => void,
  onResolved?: () => void,
) {
  const isPendingRef = useRef(false);
  const onPendingRef = useRef(onPending);
  const onResolvedRef = useRef(onResolved);
  onPendingRef.current = onPending;
  onResolvedRef.current = onResolved;

  useLangGraphInterrupt({
    enabled: ({ eventValue }) => {
      const v = eventValue?.action ?? eventValue?.args?.action;
      if (v === "confirm_delete" && !isPendingRef.current) {
        isPendingRef.current = true;
        setTimeout(() => onPendingRef.current?.(), 0);
      }
      return v === "confirm_delete";
    },
    render: ({ event, resolve: rawResolve }) => {
      const resolve = (v: string) => {
        isPendingRef.current = false;
        setTimeout(() => onResolvedRef.current?.(), 0);
        rawResolve(v);
      };
      const val = event.value ?? {};
      const args = val.args ?? val;
      const { contact_id, preview } = args;
      const contact = preview?.contact ?? {};
      const company = contact?.properties?.company || contact_id;
      const engagements = preview?.engagements ?? [];
      const submissions = preview?.submissions ?? [];
      const drafts = preview?.drafts ?? [];

      return (
        <div className="my-3 rounded-md border border-red-500/30 bg-red-50/5 overflow-hidden">
          <div className="px-4 py-2.5 bg-red-500/5 border-b border-red-500/10">
            <span className="text-sm font-semibold uppercase tracking-wider text-red-400">
              Confirm Delete
            </span>
          </div>
          <div className="px-4 py-3">
            <p className="text-sm mb-2">
              Delete <strong>{company}</strong> (ID: {contact_id}) and all data?
            </p>
            <ul className="text-sm text-text-muted mb-3 space-y-0.5">
              <li>{engagements.length} engagement(s)</li>
              <li>{submissions.length} submission(s)</li>
              <li>{drafts.length} draft(s)</li>
            </ul>
          </div>
          <DeleteActions resolve={resolve} company={company} />
        </div>
      );
    },
  });
}
