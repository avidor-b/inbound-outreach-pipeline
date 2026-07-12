"use client";

import { useState } from "react";
import { useAgent, useCopilotKit } from "@copilotkit/react-core/v2";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

const confidenceConfig: Record<
  string,
  { color: string; bg: string; label: string }
> = {
  high: { color: "text-emerald-600", bg: "bg-emerald-100", label: "High" },
  medium: { color: "text-yellow-600", bg: "bg-yellow-100", label: "Medium" },
  low: { color: "text-red-500", bg: "bg-red-100", label: "Low" },
};

interface EnrichmentResultProps {
  success?: boolean;
  contact_id: string;
  company?: string;
  confidence?: string;
  reason?: string;
  summary?: string;
  already_enriched?: boolean;
  website?: string;
  industry?: string;
  region?: string;
  error?: string;
}

export function EnrichmentResult(props: EnrichmentResultProps) {
  const {
    contact_id,
    company,
    confidence,
    reason,
    summary,
    already_enriched,
    website,
    industry,
    region,
    error,
  } = props;

  const [currentWebsite, setCurrentWebsite] = useState(website || "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(website || "");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const { agent } = useAgent();
  const { copilotkit } = useCopilotKit();

  const saveWebsite = async () => {
    setSaving(true);
    try {
      const res = await fetch(
        `${BACKEND_URL}/contacts/${encodeURIComponent(contact_id)}/website`,
        {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ website: draft.trim() }),
        },
      );
      if (res.ok) {
        const data = await res.json();
        setCurrentWebsite(data.website_override || "");
        setEditing(false);
        setDirty(true); // show re-enrich CTA until the user acts on it
      }
    } catch {
      // swallow — button stays enabled for retry
    } finally {
      setSaving(false);
    }
  };

  const triggerReEnrich = async () => {
    if (!agent) return;
    const id =
      (globalThis.crypto && "randomUUID" in globalThis.crypto
        ? (globalThis.crypto as Crypto).randomUUID()
        : String(Date.now()));
    // Send a user message so the agent runs enrich_contact again.
    agent.addMessage({
      id,
      role: "user",
      content: `Re-enrich ${company || contact_id}`,
    });
    try {
      await copilotkit.runAgent({ agent });
      setDirty(false);
    } catch {
      // user can retry; leave the CTA visible
    }
  };

  if (error) {
    return (
      <div className="my-2 px-4 py-2.5 rounded-md border border-red-500/30 bg-red-50/5 text-sm text-red-400">
        {error}
      </div>
    );
  }

  const conf = confidenceConfig[confidence || "low"] || confidenceConfig.low;
  const prettyWebsite = currentWebsite.replace(/^https?:\/\//, "");

  return (
    <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
      <div className="px-4 py-2.5 flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-sm font-semibold ${conf.bg} ${conf.color}`}
            >
              {conf.label}
            </span>
            {company && (
              <span className="text-sm font-semibold text-text">{company}</span>
            )}
            {already_enriched && (
              <span className="text-sm italic text-text-faint">
                (already enriched)
              </span>
            )}
          </div>
          <span className="text-sm text-text-faint font-mono">{contact_id}</span>
          {(industry || region) && (
            <span className="text-sm text-text-muted">
              {[industry, region].filter(Boolean).join(" · ")}
            </span>
          )}
        </div>
      </div>

      {/* Website used for enrichment — editable */}
      <div className="px-4 pb-2">
        {editing ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-muted">Website:</span>
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void saveWebsite();
                if (e.key === "Escape") {
                  setEditing(false);
                  setDraft(currentWebsite);
                }
              }}
              placeholder="https://example.com"
              className="flex-1 text-sm px-2 py-1 rounded border border-border/40 bg-background focus:outline focus:outline-2 focus:outline-primary/40"
              autoFocus
            />
            <button
              onClick={saveWebsite}
              disabled={saving}
              className="text-sm px-2 py-1 rounded bg-primary text-white hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setDraft(currentWebsite);
              }}
              className="text-sm px-2 py-1 rounded text-text-muted hover:text-text"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-text-muted">Website:</span>
            {currentWebsite ? (
              <a
                href={currentWebsite}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                {prettyWebsite}
              </a>
            ) : (
              <span className="italic text-text-faint">no website detected</span>
            )}
            <button
              onClick={() => setEditing(true)}
              className="text-sm text-text-faint hover:text-primary transition-colors"
              title="Correct the website used for enrichment"
            >
              edit
            </button>
            {dirty && (
              <button
                onClick={triggerReEnrich}
                className="ml-auto text-sm px-2 py-1 rounded bg-primary text-white hover:bg-primary/90"
                title="Re-run enrichment with the corrected website"
              >
                Re-enrich
              </button>
            )}
          </div>
        )}
      </div>

      {summary && (
        <div className="px-4 pb-2 text-sm text-text">{summary}</div>
      )}

      {reason && (
        <div className="px-4 pb-3 text-sm text-text-muted italic">{reason}</div>
      )}
    </div>
  );
}
