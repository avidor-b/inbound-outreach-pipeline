"use client";

import { useState } from "react";
import { z } from "zod";

export const DeletePreviewProps = z.object({
  contact_id: z.string(),
  company: z.string(),
  submissions_count: z.number(),
  engagements_count: z.number(),
  drafts_count: z.number(),
});

type DeletePreviewProps = z.infer<typeof DeletePreviewProps> & {
  status: "inProgress" | "executing" | "complete";
  respond?: (response: string) => void;
};

export function DeletePreview({
  status,
  respond,
  contact_id,
  company,
  submissions_count,
  engagements_count,
  drafts_count,
}: DeletePreviewProps) {
  const [decided, setDecided] = useState<"yes" | "no" | null>(null);

  if (decided === "yes") {
    return (
      <div className="my-3 max-w-md p-4 bg-red-50 border border-red-200 rounded text-center">
        <div className="font-bold text-red-800">Deleted</div>
        <div className="text-sm text-red-600">{company} ({contact_id})</div>
      </div>
    );
  }

  if (decided === "no") {
    return (
      <div className="my-3 max-w-md p-4 bg-gray-50 border border-gray-200 rounded text-center">
        <div className="font-bold">Cancelled</div>
      </div>
    );
  }

  return (
    <div className="my-3 max-w-md border border-red-200 rounded overflow-hidden">
      <div className="px-4 py-3 bg-red-50 border-b border-red-200">
        <div className="font-[family-name:var(--font-headline)] font-bold text-sm text-red-800">
          Delete {company}?
        </div>
        <div className="text-sm text-red-600 font-[family-name:var(--font-mono)]">{contact_id}</div>
      </div>
      <div className="p-4 bg-white text-sm space-y-1">
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">Submissions</span>
          <span className="font-medium">{submissions_count}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">Engagements</span>
          <span className="font-medium">{engagements_count}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">Drafts</span>
          <span className="font-medium">{drafts_count}</span>
        </div>
      </div>
      {status === "executing" && (
        <div className="p-3 bg-red-50 border-t border-red-200 flex gap-2">
          <button
            onClick={() => { setDecided("yes"); respond?.("confirmed"); }}
            className="flex-1 px-3 py-1.5 bg-red-600 text-white text-sm font-medium rounded hover:bg-red-700"
          >
            Delete
          </button>
          <button
            onClick={() => { setDecided("no"); respond?.("cancelled"); }}
            className="flex-1 px-3 py-1.5 bg-white border border-gray-300 text-sm font-medium rounded hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
