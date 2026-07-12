"use client";

import { useState } from "react";
import { z } from "zod";

export const DraftApprovalProps = z.object({
  engagement_id: z.number().describe("Engagement ID"),
  contact_name: z.string().describe("Contact or company name"),
  subject: z.string().describe("Email subject line"),
  body: z.string().describe("Full email body text"),
  score: z.number().describe("Engagement score"),
  industry: z.string().optional().describe("Contact's industry"),
  region: z.string().optional().describe("Contact's region"),
});

type DraftApprovalProps = z.infer<typeof DraftApprovalProps> & {
  status: "inProgress" | "executing" | "complete";
  respond?: (response: string) => void;
};

export function DraftApproval({
  status,
  respond,
  engagement_id,
  contact_name,
  subject,
  body,
  score,
  industry,
  region,
}: DraftApprovalProps) {
  const [feedback, setFeedback] = useState("");
  const [decided, setDecided] = useState<"approved" | "rejected" | null>(null);

  const handleApprove = () => {
    setDecided("approved");
    respond?.("approved");
  };

  const handleReject = () => {
    setDecided("rejected");
    respond?.(`rejected: ${feedback || "Please revise"}`);
  };

  if (decided === "approved") {
    return (
      <div className="my-3 max-w-2xl p-4 bg-green-50 border border-green-200 rounded text-center">
        <div className="text-2xl mb-2">✓</div>
        <div className="font-bold text-green-800">Draft Approved</div>
        <div className="text-sm text-green-600">Engagement #{engagement_id} for {contact_name}</div>
      </div>
    );
  }

  if (decided === "rejected") {
    return (
      <div className="my-3 max-w-2xl p-4 bg-amber-50 border border-amber-200 rounded text-center">
        <div className="text-2xl mb-2">↻</div>
        <div className="font-bold text-amber-800">Revision Requested</div>
        <div className="text-sm text-amber-600">{feedback || "Revising..."}</div>
      </div>
    );
  }

  return (
    <div className="my-3 max-w-2xl border border-[var(--color-border)]/30 rounded overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-surface)] border-b border-[var(--color-border)]/20">
        <div>
          <div className="font-[family-name:var(--font-headline)] font-bold text-sm">
            Draft Review — {contact_name}
          </div>
          <div className="text-sm text-[var(--color-text-muted)]">
            Engagement #{engagement_id} · Score: {score}
          </div>
          {(industry || region) && (
            <div className="text-sm text-[var(--color-text-muted)]">
              {[industry, region].filter(Boolean).join(" · ")}
            </div>
          )}
        </div>
        <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-primary)]">
          {status === "inProgress" ? "Loading..." : "Awaiting Review"}
        </div>
      </div>

      {/* Email Preview */}
      <div className="p-4 bg-white">
        <div className="mb-3">
          <div className="text-sm text-[var(--color-text-muted)] mb-1">Subject</div>
          <div className="font-medium">{subject}</div>
        </div>
        <div className="border-t border-[var(--color-border)]/15 pt-3">
          <div className="text-sm text-[var(--color-text-muted)] mb-1">Body</div>
          <div className="text-sm whitespace-pre-line leading-relaxed">{body}</div>
        </div>
      </div>

      {/* Actions */}
      {status === "executing" && (
        <div className="p-4 bg-[var(--color-surface)] border-t border-[var(--color-border)]/20">
          <div className="mb-3">
            <input
              type="text"
              placeholder="Feedback for revision (optional)"
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-[var(--color-border)]/30 rounded bg-white"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              className="flex-1 px-4 py-2 bg-[var(--color-primary)] text-white text-sm font-medium rounded hover:opacity-90"
            >
              Approve
            </button>
            <button
              onClick={handleReject}
              className="flex-1 px-4 py-2 bg-white border border-[var(--color-border)]/40 text-sm font-medium rounded hover:bg-[var(--color-surface)]"
            >
              Request Revision
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
