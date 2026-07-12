"use client";

import { useState } from "react";
import { useDefaultRenderTool, useRenderTool } from "@copilotkit/react-core/v2";
import { z } from "zod";
import { ToolReasoning } from "@/components/tool-rendering";
import { ScoreResult } from "@/components/generative-ui/score-result";
import { EnrichmentResult } from "@/components/generative-ui/enrichment-result";
import { ToolTable, RENDERERS } from "@/components/generative-ui/tool-table";
import { ToolSummary } from "@/components/generative-ui/tool-summary";
import { ToolDetail } from "@/components/generative-ui/tool-detail";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

/**
 * One-click "I sent this email from my own client" confirmation.
 *
 * Visibility is keyed off the DRAFT's status (not the engagement's)
 * because that's the user's mental model: "I see an approved
 * email, I sent it." After the Draft Review interrupt, the
 * natural state is `engagement=pending_approval, draft=approved`
 * — the backend's mark-sent endpoint walks the engagement
 * through `pending_approval → approved → sent` in one POST.
 */
function MarkAsSentButton({
  engagementId,
  draftStatus,
}: {
  engagementId: number;
  draftStatus?: string;
}) {
  const [status, setStatus] = useState(draftStatus || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (status === "sent") {
    return (
      <span className="text-sm text-emerald-700 font-semibold">
        ✓ Marked sent
      </span>
    );
  }
  if (status !== "approved") return null;

  const mark = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `${BACKEND_URL}/engagements/${engagementId}/mark-sent`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) {
        const body = await res.text();
        setError(body.slice(0, 60) || `error ${res.status}`);
        return;
      }
      const data = await res.json();
      setStatus(data.status || "sent");
    } catch {
      setError("failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={mark}
      disabled={loading}
      className="text-sm px-2 py-0.5 rounded border border-emerald-500/40 text-emerald-700 hover:bg-emerald-50/40 disabled:opacity-50"
      title="Record that you sent this email from your own client"
    >
      {loading ? "Saving…" : error || "Mark as sent"}
    </button>
  );
}

/**
 * On-demand email reveal for the drafts card. Fetches the address
 * from HubSpot via GET /contacts/{id}/email — the backend returns
 * the live value but does NOT persist it to our DB (PII-on-demand).
 */
function DraftEmailReveal({
  contactId,
  cachedEmail,
}: {
  contactId: string;
  cachedEmail?: string;
}) {
  const [email, setEmail] = useState(cachedEmail || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (email) {
    return (
      <a href={`mailto:${email}`} className="text-primary hover:underline">
        {email}
      </a>
    );
  }

  const fetchEmail = async () => {
    if (!contactId) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `${BACKEND_URL}/contacts/${encodeURIComponent(contactId)}/email`,
        { credentials: "include" },
      );
      if (!res.ok) {
        setError(res.status === 404 ? "not found" : `error ${res.status}`);
        return;
      }
      const data = await res.json();
      setEmail(data.email || "");
      if (!data.email) setError("not in HubSpot");
    } catch {
      setError("failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={fetchEmail}
      disabled={loading}
      className="text-sm text-primary hover:underline disabled:opacity-50"
      title="Fetch the email address from HubSpot without saving it locally"
    >
      {loading ? "Fetching…" : error || "Show email"}
    </button>
  );
}

/**
 * On-demand first-name reveal for the drafts card. Like email reveal,
 * this fetches live HubSpot PII and keeps it only in component state.
 */
function DraftFirstNameReveal({
  contactId,
}: {
  contactId: string;
}) {
  const [firstName, setFirstName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (firstName) {
    return (
      <span className="text-text-muted">
        First name: <span className="font-medium text-text">{firstName}</span>
      </span>
    );
  }

  const fetchFirstName = async () => {
    if (!contactId) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `${BACKEND_URL}/contacts/${encodeURIComponent(contactId)}/firstname`,
        { credentials: "include" },
      );
      if (!res.ok) {
        setError(res.status === 404 ? "not found" : `error ${res.status}`);
        return;
      }
      const data = await res.json();
      setFirstName(data.firstname || "");
      if (!data.firstname) setError("not in HubSpot");
    } catch {
      setError("failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={fetchFirstName}
      disabled={loading}
      className="text-sm text-primary hover:underline disabled:opacity-50"
      title="Fetch the first name from HubSpot without saving it locally"
    >
      {loading ? "Fetching…" : error || "Show first name"}
    </button>
  );
}

// One-line definitions surfaced via the (i) icon on each tool card.
// Keeping them centralized so every view shows the same explanation.
const GLOSSARY = {
  leads:
    "Contacts are the people we're tracking (identity: email, company, job title). A 'lead' is a contact who's shown interest via a form submission.",
  contacts:
    "Contacts are the people we're tracking from HubSpot — identity data only (email, company, job title, country).",
  engagements:
    "An Engagement is a unit of outreach work: one per product interest per contact. It moves through states (open → scoring → drafting → pending_approval → approved → sent → closed/blocked, or sdr_handoff when flagged for human follow-up).",
  submissions:
    "A Submission is each time a contact filled a form or visited a lead-capture page. It's the EVIDENCE — the 'why' behind reaching out. Multiple submissions usually roll up into one engagement.",
  drafts:
    "A Draft is a specific outbound email attached to an engagement. An engagement can have multiple revisions; the latest approved draft is what gets sent.",
  pipeline_stats:
    "Pipeline volumes at a glance: how many contacts, engagements, drafts, sends, replies at each stage.",
  pipeline_runs:
    "A Pipeline Run is one invocation of the ingest→enrich→score→draft→review flow for a date range.",
  replies:
    "Replies are incoming emails classified by intent and urgency, so hot leads surface first.",
} as const;

function parseResult(result: any): any {
  if (!result) return {};
  if (typeof result !== "string") return result;
  // Try direct JSON parse first
  try { return JSON.parse(result); } catch { /* fall through */ }
  // Tool results may have an ID summary prefix before the JSON
  // (e.g. "IDs: 123 (Acme), 456 (Beta)\n{...}"). Find the first '{'
  // and parse from there.
  const jsonStart = result.indexOf("{");
  if (jsonStart > 0) {
    try { return JSON.parse(result.slice(jsonStart)); } catch { /* fall through */ }
  }
  // Also try '[' for array results
  const arrStart = result.indexOf("[");
  if (arrStart > 0) {
    try { return JSON.parse(result.slice(arrStart)); } catch { /* fall through */ }
  }
  return { _text: result };
}

export function useOutreachUI() {
  // Default: compact tool call indicator for all tools
  useDefaultRenderTool({
    render: ({ name, status, parameters }) => {
      return <ToolReasoning name={name} status={status} args={parameters} />;
    },
  });

  // ---- Score result (existing) ----
  useRenderTool({
    name: "score_engagement",
    parameters: z.object({ engagement_id: z.number() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete" || !result) {
        return <ToolReasoning name="score_engagement" status={status} args={{ engagement_id: parameters?.engagement_id }} />;
      }
      let text = typeof result === "string" ? result : JSON.stringify(result);
      // Pop end-anchored meta suffixes (contact_message, region,
      // industry) before the reasoning regex runs so they don't get
      // swallowed into the reasoning field.
      const msgMatch = text.match(/\s*\|contact_message:([^|]+?)\s*$/);
      const contactMessage = msgMatch ? msgMatch[1].trim() : "";
      if (msgMatch) text = text.slice(0, msgMatch.index).trimEnd();
      const regionMatch = text.match(/\s*\|region:([^|]+?)\s*$/);
      const region = regionMatch ? regionMatch[1].trim() : "";
      if (regionMatch) text = text.slice(0, regionMatch.index).trimEnd();
      const industryMatch = text.match(/\s*\|industry:([^|]+?)\s*$/);
      const industry = industryMatch ? industryMatch[1].trim() : "";
      if (industryMatch) text = text.slice(0, industryMatch.index).trimEnd();
      const companyMatch = text.match(/^\[([^\]]+)\]/);
      const eidMatch = text.match(/#(\d+)/);
      const scoreMatch = text.match(/(\d+)\/100/);
      const productMatch = text.match(/\(([^)]+)\)/);
      const statusMatch = text.match(/→\s*(\w+)/);
      // Multi-line: dimensions row + "• <dim>: evidence" bullets.
      const reasoningMatch = text.match(/\.\s+([\s\S]+)$/);
      return (
        <ScoreResult
          engagement_id={eidMatch ? parseInt(eidMatch[1]) : parameters?.engagement_id ?? 0}
          company={companyMatch?.[1] || ""}
          score={scoreMatch ? parseInt(scoreMatch[1]) : 0}
          primary_product={productMatch?.[1] || ""}
          status={statusMatch?.[1] || ""}
          score_reasoning={reasoningMatch?.[1] || ""}
          industry={industry}
          region={region}
          contact_message={contactMessage}
          already_scored={text.includes("already scored")}
          skipped={text.includes("skipped") || text.includes("blocked")}
          reason={text.includes("skipped") || text.includes("blocked") ? text : undefined}
        />
      );
    },
  }, []);

  // ---- Enrichment result (existing) ----
  useRenderTool({
    name: "enrich_contact",
    parameters: z.object({ contact_id: z.string() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete" || !result) {
        return <ToolReasoning name="enrich_contact" status={status} args={{ contact_id: parameters?.contact_id }} />;
      }
      let text = typeof result === "string" ? result : JSON.stringify(result);
      // Backend appends meta suffixes in order industry → region →
      // website. Pop them right-to-left so each end-anchored regex
      // stays simple and can't swallow a prior suffix's value.
      const webMatch = text.match(/\s*\|website:(\S+)\s*$/);
      const website = webMatch ? webMatch[1] : "";
      if (webMatch) text = text.slice(0, webMatch.index).trimEnd();
      const regionMatch = text.match(/\s*\|region:([^|]+?)\s*$/);
      const region = regionMatch ? regionMatch[1].trim() : "";
      if (regionMatch) text = text.slice(0, regionMatch.index).trimEnd();
      const industryMatch = text.match(/\s*\|industry:([^|]+?)\s*$/);
      const industry = industryMatch ? industryMatch[1].trim() : "";
      if (industryMatch) text = text.slice(0, industryMatch.index).trimEnd();
      const companyMatch = text.match(/^\[([^\]]+)\]/);
      const cidMatch = text.match(/Enriched\s+(\S+)/i);
      const confMatch = text.match(/\((high|medium|low)\)/i);
      const summaryMatch = text.match(/\):\s*(.+)/);
      return (
        <EnrichmentResult
          contact_id={cidMatch?.[1] || parameters?.contact_id || ""}
          company={companyMatch?.[1] || ""}
          confidence={confMatch?.[1]?.toLowerCase() || "medium"}
          summary={summaryMatch?.[1] || text}
          already_enriched={text.toLowerCase().includes("already enriched")}
          website={website}
          industry={industry}
          region={region}
          error={text.includes("not found") ? text : undefined}
        />
      );
    },
  }, []);

  // ---- enrich_contacts (batch) — renders one EnrichmentResult per result ----
  useRenderTool({
    name: "enrich_contacts",
    parameters: z.object({ contacts: z.string() }),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="enrich_contacts" status={status} />;
      const data = parseResult(result);
      const results = data.results || [];
      if (results.length === 0) {
        return (
          <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm text-text-muted">
            No contacts processed.
          </div>
        );
      }
      return (
        <div className="space-y-1">
          {results.map((r: any, i: number) => (
            <EnrichmentResult
              key={r.contact_id || i}
              contact_id={r.contact_id || r.contact || ""}
              company={r.contact || ""}
              confidence={r.confidence || "medium"}
              summary={r.summary || r.contact}
              already_enriched={!!r.already_enriched}
              website={r.website || ""}
              industry={r.industry || ""}
              region={r.region || ""}
              error={r.error || undefined}
            />
          ))}
        </div>
      );
    },
  }, []);

  // ---- score_contacts (batch) — renders one ScoreResult per result ----
  useRenderTool({
    name: "score_contacts",
    parameters: z.object({ contacts: z.string() }).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="score_contacts" status={status} />;
      const data = parseResult(result);
      const results = data.results || [];
      if (results.length === 0) {
        return (
          <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm text-text-muted">
            No engagements scored.
          </div>
        );
      }
      return (
        <div className="space-y-1">
          {results.map((r: any, i: number) => (
            <ScoreResult
              key={r.engagement_id || i}
              engagement_id={r.engagement_id || 0}
              company={r.contact || ""}
              jobtitle={r.jobtitle || ""}
              country={r.country || ""}
              region={r.region || ""}
              industry={r.industry || ""}
              contact_message={r.contact_message || ""}
              score={r.score || 0}
              primary_product={r.primary_product || ""}
              status={r.status || ""}
              score_reasoning={r.score_reasoning || ""}
              already_scored={!!r.already_scored}
              skipped={!!r.skipped}
              reason={r.reason || undefined}
              error={r.error || undefined}
            />
          ))}
        </div>
      );
    },
  }, []);

  // ---- get_leads / search_contacts ----
  useRenderTool({
    name: "get_leads",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_leads" status={status} />;
      const data = parseResult(result);
      return (
        <ToolTable
          title="Leads"
          count={data.count ?? data.leads?.length}
          rows={data.leads || []}
          emptyMessage="No leads found for the given filters."
          helpText={GLOSSARY.leads}
          columns={[
            { key: "id", label: "ID", width: "100px", render: RENDERERS.id },
            { key: "company", label: "Company" },
            {
              key: "industry_region",
              label: "Industry · Region",
              width: "200px",
              render: (_v, row) =>
                [row.industry, row.region].filter(Boolean).join(" · ") || (
                  <span className="text-text-faint">--</span>
                ),
            },
            { key: "score", label: "Score", width: "60px", render: RENDERERS.score },
            { key: "enriched", label: "Enriched", width: "70px", render: RENDERERS.boolean },
          ]}
        />
      );
    },
  }, []);

  useRenderTool({
    name: "search_contacts",
    parameters: z.object({ company: z.string().optional() }).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="search_contacts" status={status} />;
      const data = parseResult(result);
      return (
        <ToolTable
          title="Search Results"
          count={data.count}
          rows={data.contacts || []}
          emptyMessage="No contacts matched the search."
          helpText={GLOSSARY.contacts}
          columns={[
            { key: "id", label: "ID", width: "100px", render: RENDERERS.id },
            { key: "company", label: "Company" },
            {
              key: "industry_region",
              label: "Industry · Region",
              width: "200px",
              render: (_v, row) =>
                [row.industry, row.region].filter(Boolean).join(" · ") || (
                  <span className="text-text-faint">--</span>
                ),
            },
            { key: "score", label: "Score", width: "60px", render: RENDERERS.score },
            { key: "enriched", label: "Enriched", width: "70px", render: RENDERERS.boolean },
          ]}
        />
      );
    },
  }, []);

  // ---- get_engagements ----
  useRenderTool({
    name: "get_engagements",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_engagements" status={status} />;
      const data = parseResult(result);
      return (
        <ToolTable
          title="Engagements"
          count={data.count}
          rows={data.engagements || []}
          emptyMessage="No engagements found."
          helpText={GLOSSARY.engagements}
          columns={[
            { key: "id", label: "ID", width: "60px", render: RENDERERS.id },
            { key: "company", label: "Company" },
            {
              key: "industry_region",
              label: "Industry · Region",
              width: "200px",
              render: (_v, row) =>
                [row.industry, row.region].filter(Boolean).join(" · ") || (
                  <span className="text-text-faint">--</span>
                ),
            },
            { key: "kind", label: "Kind", width: "80px" },
            { key: "status", label: "Status", width: "80px", render: RENDERERS.status },
            { key: "score", label: "Score", width: "60px", render: RENDERERS.score },
            { key: "primary_product", label: "Product", width: "100px" },
          ]}
        />
      );
    },
  }, []);

  // ---- ingest_contacts ----
  useRenderTool({
    name: "ingest_contacts",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="ingest_contacts" status={status} />;
      const data = parseResult(result);
      const contacts = data.contacts || [];
      if (data.error) {
        return (
          <div className="my-2 px-4 py-2.5 rounded-md border border-red-500/30 bg-red-50/5 text-sm text-red-400">
            {data.error}
          </div>
        );
      }
      return (
        <ToolTable
          title="Ingested Contacts"
          count={contacts.length}
          rows={contacts}
          emptyMessage="No new contacts found for the given date range."
          helpText={GLOSSARY.contacts}
          columns={[
            { key: "contact_id", label: "Contact ID", width: "120px", render: RENDERERS.id },
            { key: "engagement_id", label: "Eng. ID", width: "70px", render: RENDERERS.id },
            { key: "company", label: "Company" },
          ]}
        />
      );
    },
  }, []);

  // ---- get_drafts ----
  useRenderTool({
    name: "get_drafts",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_drafts" status={status} />;
      const data = parseResult(result);
      const drafts = data.drafts || [];
      if (drafts.length === 0) {
        const breakdown = data.by_status || {};
        const breakdownEntries = Object.entries(breakdown) as [string, number][];
        const total = data.total_drafts || 0;
        return (
          <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm">
            <p className="text-text-muted">
              No drafts match that filter
              {data.filter?.status ? ` (status=${data.filter.status})` : ""}.
            </p>
            {total > 0 && (
              <p className="text-sm text-text-faint mt-1">
                {total} draft{total !== 1 ? "s" : ""} exist overall:{" "}
                {breakdownEntries
                  .map(([s, c]) => `${c} ${s}`)
                  .join(", ")}
                . Ask to see them with a different status (e.g. "show approved drafts").
              </p>
            )}
          </div>
        );
      }
      const statusColors: Record<string, string> = {
        draft: "bg-blue-100 text-blue-700",
        approved: "bg-emerald-100 text-emerald-700",
        sent: "bg-emerald-100 text-emerald-700",
        rejected: "bg-red-100 text-red-700",
        pending_approval: "bg-amber-100 text-amber-700",
        sdr_handoff: "bg-sky-100 text-sky-700",
      };
      return (
        <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
          <div className="px-4 py-2 bg-primary/5 border-b border-border/20 flex items-center justify-between">
            <span className="text-sm font-semibold uppercase tracking-wider text-primary flex items-center gap-1.5">
              Drafts
              <span
                tabIndex={0}
                title={GLOSSARY.drafts}
                aria-label={GLOSSARY.drafts}
                className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/15 text-primary text-[10px] font-bold leading-none cursor-help focus:outline focus:outline-2 focus:outline-primary/40"
              >
                i
              </span>
            </span>
            <span className="text-sm font-mono text-text-muted">
              {drafts.length} result{drafts.length !== 1 ? "s" : ""}
            </span>
          </div>
          <div className="divide-y divide-border/10">
            {drafts.map((d: any, i: number) => (
              <div key={d.id || i} className="px-4 py-3">
                <div className="flex items-start justify-between gap-2 mb-2 flex-wrap">
                  <div className="min-w-0">
                    {d.company && (
                      <p className="text-sm font-semibold text-text leading-tight">
                        {d.company}
                      </p>
                    )}
                    {d.contact_name && (
                      <p className="text-sm text-text-muted leading-tight">
                        {d.contact_name}
                      </p>
                    )}
                    {(d.industry || d.region) && (
                      <p className="text-sm text-text-faint leading-tight">
                        {[d.industry, d.region].filter(Boolean).join(" · ")}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-wrap shrink-0">
                    <span className={`px-1.5 py-0.5 rounded text-sm font-semibold uppercase ${statusColors[d.status] || "bg-gray-100 text-gray-600"}`}>
                      {d.status}
                    </span>
                    {d.score_at_send > 0 && (
                      <span className="text-sm text-text-muted">
                        score {d.score_at_send}
                      </span>
                    )}
                    {d.created_at && (
                      <span className="text-sm text-text-faint">
                        {String(d.created_at).slice(0, 10)}
                      </span>
                    )}
                  </div>
                </div>

                {(d.contact_id || d.contact_website || d.engagement_id) && (
                  <div className="flex items-center gap-3 flex-wrap mb-2 text-sm">
                    {d.contact_id && (
                      <DraftEmailReveal
                        contactId={d.contact_id}
                        cachedEmail={d.contact_email}
                      />
                    )}
                    {d.contact_id && (
                      <DraftFirstNameReveal
                        contactId={d.contact_id}
                      />
                    )}
                    {d.engagement_id && (
                      <MarkAsSentButton
                        engagementId={d.engagement_id}
                        draftStatus={d.status}
                      />
                    )}
                    {d.contact_website && (
                      <a
                        href={d.contact_website}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                      >
                        {d.contact_website.replace(/^https?:\/\//, "")}
                      </a>
                    )}
                    {d.engagement_id && (
                      <span className="font-mono text-text-faint">
                        eng #{d.engagement_id}
                      </span>
                    )}
                    {d.contact_id && (
                      <span className="font-mono text-text-faint">
                        contact {d.contact_id}
                      </span>
                    )}
                  </div>
                )}

                {d.original_message && (
                  <details className="mb-2 rounded border border-border/20 bg-background/50">
                    <summary className="cursor-pointer px-2 py-1 text-sm uppercase tracking-wider text-text-muted">
                      Original message
                      {d.original_form && ` · ${d.original_form}`}
                      {d.original_intent && ` · ${d.original_intent}`}
                    </summary>
                    <pre className="whitespace-pre-wrap text-sm text-text-muted px-2 pb-2 leading-relaxed">
                      {d.original_message}
                    </pre>
                  </details>
                )}

                <p className="text-sm font-medium mb-2">{d.subject}</p>
                {d.body && (
                  <pre className="whitespace-pre-wrap text-sm text-text-muted bg-background rounded p-2 border border-border/20 leading-relaxed">
                    {d.body}
                  </pre>
                )}
                {Array.isArray(d.promised_resources) && d.promised_resources.length > 0 && (
                  <div className="mt-2">
                    <p className="text-sm text-text-faint uppercase tracking-wider mb-1">
                      Resources available
                    </p>
                    <ul className="space-y-1">
                      {d.promised_resources.map((r: any) => {
                        const referenced = Array.isArray(d.referenced_resource_ids)
                          && d.referenced_resource_ids.includes(r.id);
                        return (
                          <li
                            key={r.id}
                            className={`rounded border px-2 py-1.5 ${
                              referenced
                                ? "border-emerald-500/30 bg-emerald-50/5"
                                : "border-border/20 bg-background"
                            }`}
                          >
                            <div className="flex items-start gap-2">
                              <a
                                href={r.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex-1 min-w-0 text-sm font-medium text-primary hover:underline truncate"
                                title={r.title}
                              >
                                {r.title}
                              </a>
                              <div className="flex items-center gap-1 shrink-0">
                                {r.asset_type && (
                                  <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase tracking-wider bg-border/30 text-text-muted">
                                    {String(r.asset_type).replace(/_/g, " ")}
                                  </span>
                                )}
                                {referenced && (
                                  <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase tracking-wider bg-emerald-100 text-emerald-700">
                                    ✓ Cited
                                  </span>
                                )}
                              </div>
                            </div>
                            {r.summary && (
                              <p className="mt-0.5 text-sm text-text-muted leading-snug line-clamp-2">
                                {r.summary}
                              </p>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
                {Array.isArray(d.visited_pages) && d.visited_pages.length > 0 && (
                  <div className="mt-2">
                    <p className="text-sm text-text-faint uppercase tracking-wider mb-1">
                      Pages visited
                    </p>
                    <ul className="space-y-0.5">
                      {d.visited_pages.map((p: any) => {
                        let pretty = p.url as string;
                        try {
                          const u = new URL(pretty);
                          const segs = u.pathname.replace(/\/$/, "").split("/").filter(Boolean);
                          pretty = segs[segs.length - 1] || u.hostname;
                          pretty = pretty.replace(/-/g, " ");
                        } catch { /* keep raw */ }
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
            ))}
          </div>
        </div>
      );
    },
  }, []);

  // ---- get_pipeline_stats ----
  useRenderTool({
    name: "get_pipeline_stats",
    parameters: z.object({}),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_pipeline_stats" status={status} />;
      const data = parseResult(result);
      const dr = data.date_range;
      const rangeLabel =
        dr && dr.after !== "all-time"
          ? `${dr.after}${dr.before !== "all-time" ? ` to ${dr.before}` : "+"}`
          : "";
      const draftByStatus = (data.drafts?.by_status || {}) as Record<string, number>;
      const draftBreakdownParts = Object.entries(draftByStatus)
        .sort((a, b) => b[1] - a[1])
        .map(([s, c]) => `${c} ${s}`);
      const engStatus = (data.engagements?.by_status || {}) as Record<string, number>;
      // Show every non-zero status — truncating to top-N made the hint
      // not add up to the headline count (e.g. "3+2+2=7" on "9").
      const engStatusParts = Object.entries(engStatus)
        .filter(([, c]) => c > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([s, c]) => `${c} ${s}`);
      const stats = [
        { label: "Contacts", value: data.contacts?.total ?? 0 },
        { label: "Enriched", value: data.contacts?.enriched ?? 0, color: "text-blue-600" },
        {
          label: "Engagements",
          value: data.engagements?.total ?? 0,
          hint: engStatusParts.length ? engStatusParts.join(", ") : undefined,
        },
        { label: "Scored", value: (data.engagements?.score_distribution?.high ?? 0) + (data.engagements?.score_distribution?.medium ?? 0) + (data.engagements?.score_distribution?.low ?? 0), color: "text-emerald-600" },
        {
          label: "Drafts",
          value: data.drafts?.total ?? 0,
          color: "text-purple-600",
          hint: draftBreakdownParts.length ? draftBreakdownParts.join(", ") : undefined,
        },
        { label: "Sent", value: data.outreach?.sent ?? 0, color: "text-emerald-600" },
        { label: "SDR handoff", value: data.engagements?.by_status?.sdr_handoff ?? 0, color: "text-blue-600" },
        { label: "Blocked", value: data.engagements?.by_status?.blocked ?? 0, color: "text-red-500" },
        { label: "Submissions", value: data.submissions?.total ?? 0 },
      ];
      return <ToolSummary title={rangeLabel ? `Pipeline Stats (${rangeLabel})` : "Pipeline Stats"} stats={stats} helpText={GLOSSARY.pipeline_stats} />;
    },
  }, []);

  // ---- get_lead_details ----
  useRenderTool({
    name: "get_lead_details",
    parameters: z.object({ contact_id: z.string() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete") return <ToolReasoning name="get_lead_details" status={status} />;
      const data = parseResult(result);
      // The tool returns {contact: {id, properties: {...}, ...}, engagements: [...]}
      // but _sanitize may flatten or the result may come as a different shape.
      // Try multiple paths to find the actual data.
      const c = data.contact || data;
      const props = c.properties || c.props || c;
      const engagements = data.engagements || c.engagements || [];
      return (
        <ToolDetail
          title="Contact Details"
          subtitle={parameters?.contact_id}
          sections={[
            {
              title: "Contact",
              fields: [
                { label: "Company", value: props.company },
                { label: "Industry", value: props.industry },
                { label: "Job title", value: props.jobtitle },
                { label: "Country", value: props.country },
                { label: "Region", value: props.region },
                { label: "Email domain", value: props.email_domain || c.email_domain },
                { label: "Lifecycle", value: (() => {
                  const v = props.lifecyclestage;
                  if (!v) return "";
                  if (typeof v === "string" && !/^\d+$/.test(v)) return v;
                  const map: Record<string, string> = {
                    subscriber: "Subscriber", lead: "Lead",
                    marketingqualifiedlead: "MQL", salesqualifiedlead: "SQL",
                    opportunity: "Opportunity", customer: "Customer",
                    evangelist: "Evangelist", other: "Other",
                  };
                  return map[v] || v;
                })() },
                { label: "Created", value: props.createdate?.split("T")[0] },
                { label: "Modified", value: props.lastmodifieddate?.split("T")[0] },
                { label: "Score", value: c.score || props.score, render: RENDERERS.score },
                { label: "Source", value: props.hs_analytics_source },
                { label: "Conversion", value: props.recent_conversion_event_name || props.hs_analytics_first_url, wide: true },
                { label: "Page views", value: props.hs_analytics_num_page_views },
                { label: "Email opens", value: props.hs_email_open },
                { label: "Email clicks", value: props.hs_email_click },
              ],
            },
            ...(props.message
              ? [{
                  title: "Message",
                  fields: [
                    { label: "Contact said", value: props.message, wide: true },
                  ],
                }]
              : []),
            ...(c.company_context
              ? [{
                  title: "Enrichment",
                  fields: [
                    { label: "Company context", value: c.company_context, wide: true },
                  ],
                }]
              : []),
            ...(engagements.length
              ? [{
                  title: `Engagements (${engagements.length})`,
                  fields: engagements.map((e: any) => ({
                    label: `#${e.id} (${e.kind})`,
                    value: e.status,
                    render: RENDERERS.status,
                  })),
                }]
              : []),
          ]}
        />
      );
    },
  }, []);

  // ---- get_engagement_details ----
  useRenderTool({
    name: "get_engagement_details",
    parameters: z.object({ engagement_id: z.number() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete") return <ToolReasoning name="get_engagement_details" status={status} />;
      const data = parseResult(result);
      const eng = data.engagement || data;
      const submissions = data.submissions || eng.submissions || [];
      return (
        <ToolDetail
          title="Engagement Details"
          subtitle={`#${parameters?.engagement_id}`}
          sections={[
            {
              title: "Engagement",
              fields: [
                { label: "Status", value: eng.status, render: RENDERERS.status },
                { label: "Kind", value: eng.kind },
                { label: "Score", value: eng.score, render: RENDERERS.score },
                { label: "Product", value: eng.primary_product },
                { label: "Intent", value: eng.buying_intent },
                { label: "Reasoning", value: eng.score_reasoning },
              ],
            },
            ...(submissions.length
              ? [{
                  title: `Submissions (${submissions.length})`,
                  fields: submissions.map((s: any, i: number) => ({
                    label: `#${i + 1} (${s.intent_type || "unknown"})`,
                    value: (s.message || "").slice(0, 200),
                  })),
                }]
              : []),
          ]}
        />
      );
    },
  }, []);

  // ---- get_submissions ----
  // Shared card for one contact's submission list. Used by both the
  // single `get_submissions` and bulk `get_submissions_bulk` tools so
  // the two paths render identical cards (and new fields — e.g.
  // message_translation — pick up in both at once).
  const renderSubmissionsCard = (data: any, fallbackId?: string) => {
    const subs = data?.submissions || [];
    if (subs.length === 0) {
      return (
        <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm text-text-muted">
          No submissions found for contact {fallbackId || data?.contact_id}.
        </div>
      );
    }
    return (
      <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
        <div className="px-4 py-2 bg-primary/5 border-b border-border/20 flex items-center justify-between">
          <span className="text-sm font-semibold uppercase tracking-wider text-primary flex items-center gap-1.5">
            Submissions
            <span
              tabIndex={0}
              title={GLOSSARY.submissions}
              aria-label={GLOSSARY.submissions}
              className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/15 text-primary text-[10px] font-bold leading-none cursor-help focus:outline focus:outline-2 focus:outline-primary/40"
            >
              i
            </span>
          </span>
          <span className="text-sm text-text-muted">
            {data.count || subs.length} for{" "}
            {data.company ? (
              <span className="font-semibold text-text">{data.company}</span>
            ) : (
              <span className="font-mono">{data.contact_id}</span>
            )}
          </span>
        </div>
        <div className="divide-y divide-border/10">
          {subs.map((s: any, i: number) => (
            <div key={s.id || i} className="px-4 py-3">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <span className="font-mono text-sm text-text-faint">#{s.id}</span>
                <span className="text-sm text-text-muted">{s.date}</span>
                {s.intent_type && (
                  <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase bg-blue-100 text-blue-700">
                    {s.intent_type}
                  </span>
                )}
                {s.product_family && s.product_family !== "other" && (
                  <span className="px-1.5 py-0.5 rounded text-sm font-semibold uppercase bg-purple-100 text-purple-700">
                    {s.product_family}
                  </span>
                )}
              </div>
              {(() => {
                const url = (s.page_url || "").trim();
                const formLabel = (s.form_name || "").trim();
                if (url) {
                  let pretty = url;
                  try {
                    const u = new URL(url);
                    const segs = u.pathname.replace(/\/$/, "").split("/").filter(Boolean);
                    pretty = segs[segs.length - 1] || u.hostname;
                    pretty = pretty.replace(/-/g, " ");
                  } catch { /* keep raw url */ }
                  return (
                    <p className="text-sm text-text-faint mb-1">
                      Page:{" "}
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                        title={url}
                      >
                        {pretty}
                      </a>
                      {formLabel && <span className="text-text-faint"> · {formLabel}</span>}
                    </p>
                  );
                }
                if (formLabel) {
                  return <p className="text-sm text-text-faint mb-1">Form: {formLabel}</p>;
                }
                return null;
              })()}
              {s.message ? (
                <>
                  <p className="text-sm text-text-muted whitespace-pre-wrap leading-relaxed">
                    &ldquo;{s.message}&rdquo;
                  </p>
                  {s.message_translation && (
                    <p
                      className="mt-1 text-sm text-text-faint italic whitespace-pre-wrap leading-relaxed border-l-2 border-border/30 pl-2"
                      title="LLM-generated English translation attached at ingestion time"
                    >
                      EN: {s.message_translation}
                    </p>
                  )}
                </>
              ) : (
                <p
                  className="text-sm text-text-faint italic"
                  title="HubSpot stores only one 'message' property per contact — always the latest. Older form submissions therefore appear messageless even if the user typed something at the time."
                >
                  No message on record — HubSpot keeps only the latest
                  message text per contact, so earlier submissions
                  show empty even when they originally had content.
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  };

  useRenderTool({
    name: "get_submissions",
    parameters: z.object({ contact_id: z.string() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete") return <ToolReasoning name="get_submissions" status={status} />;
      const data = parseResult(result);
      return renderSubmissionsCard(data, parameters?.contact_id);
    },
  }, []);

  useRenderTool({
    name: "get_submissions_bulk",
    parameters: z.object({ contacts: z.string() }),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_submissions_bulk" status={status} />;
      const data = parseResult(result);
      const results = data?.results || [];
      if (results.length === 0) {
        return (
          <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm text-text-muted">
            No submissions found for the requested contacts.
          </div>
        );
      }
      return (
        <div className="space-y-1">
          {results.map((r: any, i: number) => (
            <div key={r.contact_id || i}>{renderSubmissionsCard(r)}</div>
          ))}
        </div>
      );
    },
  }, []);

  // ---- get_contact_history ----
  useRenderTool({
    name: "get_contact_history",
    parameters: z.object({ contact_id: z.string() }),
    render: ({ status, result, parameters }) => {
      if (status !== "complete") return <ToolReasoning name="get_contact_history" status={status} />;
      const data = parseResult(result);
      const c = data.contact || data;
      const props = c.properties || c;
      const engagements = data.engagements || [];
      const submissions = data.submissions || [];
      return (
        <ToolDetail
          title="Contact History"
          subtitle={parameters?.contact_id}
          sections={[
            {
              title: "Contact",
              fields: [
                { label: "Company", value: props.company },
                { label: "Industry", value: props.industry },
                { label: "Job title", value: props.jobtitle },
                { label: "Country", value: props.country },
                { label: "Region", value: props.region },
                { label: "Email domain", value: props.email_domain || c.email_domain },
                { label: "Source", value: props.hs_analytics_source },
              ],
            },
            ...(engagements.length
              ? [{
                  title: `Engagements (${engagements.length})`,
                  fields: engagements.map((e: any) => ({
                    label: `#${e.id} ${e.kind}`,
                    value: `${e.status}${e.score ? ` · ${e.score}/100` : ""}${e.primary_product ? ` · ${e.primary_product}` : ""}`,
                  })),
                }]
              : []),
            ...(submissions.length
              ? [{
                  title: `Submissions (${submissions.length})`,
                  fields: submissions.map((s: any) => ({
                    label: `${String(s.mql_date || s.date || "").slice(0, 10)} · ${s.intent_type || "unknown"}`,
                    value: (s.message || "").slice(0, 200) || "(no message)",
                    wide: true,
                  })),
                }]
              : []),
          ]}
        />
      );
    },
  }, []);

  // ---- get_pipeline_runs ----
  useRenderTool({
    name: "get_pipeline_runs",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="get_pipeline_runs" status={status} />;
      const data = parseResult(result);
      return (
        <ToolTable
          title="Pipeline Runs"
          count={data.count}
          rows={data.runs || []}
          emptyMessage="No pipeline runs recorded."
          helpText={GLOSSARY.pipeline_runs}
          columns={[
            { key: "id", label: "Run", width: "50px", render: RENDERERS.id },
            { key: "start_date", label: "Start" },
            { key: "end_date", label: "End" },
            { key: "contacts_fetched", label: "Fetched", width: "70px" },
            { key: "emails_drafted", label: "Drafted", width: "70px" },
          ]}
        />
      );
    },
  }, []);

  // ---- web_search ----
  useRenderTool({
    name: "web_search",
    parameters: z.object({ query: z.string() }),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="web_search" status={status} />;
      const data = parseResult(result);
      const results = data.results || [];
      return (
        <ToolTable
          title="Web Search"
          count={results.length}
          rows={results}
          emptyMessage="No results found."
          columns={[
            { key: "title", label: "Title", render: (v: any, row: any) => (
              <a href={row.url} target="_blank" rel="noopener noreferrer"
                className="text-primary hover:underline">{v}</a>
            )},
            { key: "snippet", label: "Snippet", render: RENDERERS.truncate },
          ]}
        />
      );
    },
  }, []);

  // ---- monitor_replies ----
  useRenderTool({
    name: "monitor_replies",
    parameters: z.object({}).passthrough(),
    render: ({ status, result }) => {
      if (status !== "complete") return <ToolReasoning name="monitor_replies" status={status} />;
      const data = parseResult(result);
      const replies = data.replies || [];
      return (
        <ToolTable
          title="Replies"
          count={replies.length}
          rows={replies}
          emptyMessage="No replies found."
          helpText={GLOSSARY.replies}
          columns={[
            { key: "contact_id", label: "Contact", width: "100px", render: RENDERERS.id },
            { key: "urgency", label: "Urgency", width: "70px" },
            { key: "sentiment", label: "Sentiment", width: "80px" },
            { key: "body_preview", label: "Preview", render: RENDERERS.truncate },
          ]}
        />
      );
    },
  }, []);
}
