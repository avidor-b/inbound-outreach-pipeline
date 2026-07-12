"use client";

import { z } from "zod";

const Submission = z.object({
  id: z.number(),
  mql_date: z.string(),
  intent_type: z.string(),
  form_name: z.string().optional(),
  page_url: z.string().optional(),
  message: z.string().optional(),
  message_translation: z.string().optional(),
  product_family: z.string().optional(),
});

const Engagement = z.object({
  id: z.number(),
  kind: z.string(),
  status: z.string(),
  score: z.number(),
  primary_product: z.string().optional(),
});

export const ContactDetailProps = z.object({
  contact_id: z.string(),
  company: z.string().describe("Company name"),
  score: z.number().describe("Contact score"),
  buying_intent: z.string().optional(),
  company_context: z.string().optional().describe("Enrichment summary"),
  submissions: z.array(Submission).describe("Submission history"),
  engagements: z.array(Engagement).describe("Engagement list"),
});

type ContactDetailProps = z.infer<typeof ContactDetailProps>;

const intentColors: Record<string, string> = {
  sales_inquiry: "bg-blue-100 text-blue-700",
  support_request: "bg-orange-100 text-orange-700",
  content_download: "bg-gray-100 text-gray-600",
  event_meeting: "bg-purple-100 text-purple-700",
};

export function ContactDetail(props: Record<string, unknown>) {
  const result = ContactDetailProps.safeParse(props);
  if (!result.success) {
    return <div className="my-3 p-4 text-sm text-[var(--color-text-muted)] animate-pulse">Loading contact details...</div>;
  }
  const { contact_id, company, score, buying_intent, company_context, submissions, engagements } = result.data;
  return (
    <div className="my-3 max-w-2xl space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between p-4 bg-white border border-[var(--color-border)]/20 rounded">
        <div>
          <div className="font-[family-name:var(--font-headline)] font-bold text-lg">{company}</div>
          <div className="text-sm text-[var(--color-text-muted)] font-[family-name:var(--font-mono)]">{contact_id}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold font-[family-name:var(--font-headline)]">{score}</div>
          <div className="text-sm text-[var(--color-text-muted)]">{buying_intent || "not scored"}</div>
        </div>
      </div>

      {/* Context */}
      {company_context && (
        <div className="p-3 bg-[var(--color-surface)] rounded text-sm text-[var(--color-text-muted)]">
          {company_context}
        </div>
      )}

      {/* Submissions */}
      <div>
        <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
          Submissions ({submissions.length})
        </div>
        <div className="space-y-2">
          {submissions.map((s) => (
            <div key={s.id} className="p-3 bg-white border border-[var(--color-border)]/15 rounded text-sm">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-sm text-[var(--color-text-muted)]">#{s.id}</span>
                <span className="text-sm text-[var(--color-text-muted)]">{s.mql_date?.slice(0, 10)}</span>
                <span className={`px-1.5 py-0.5 rounded text-sm font-medium ${intentColors[s.intent_type] || "bg-gray-100"}`}>
                  {s.intent_type}
                </span>
                {s.product_family && s.product_family !== "other" && (
                  <span className="text-sm text-[var(--color-primary)] font-medium">{s.product_family}</span>
                )}
              </div>
              {s.form_name && <div className="text-sm text-[var(--color-text-muted)]">Form: {s.form_name}</div>}
              {s.page_url && <div className="text-sm text-[var(--color-primary)] truncate">{s.page_url}</div>}
              {s.message && <div className="mt-1 text-sm">{s.message}</div>}
              {s.message_translation && (
                <div
                  className="mt-1 text-sm italic text-[var(--color-text-muted)] border-l-2 border-[var(--color-border)]/30 pl-2"
                  title="LLM-generated English translation"
                >
                  EN: {s.message_translation}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Engagements */}
      {engagements.length > 0 && (
        <div>
          <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
            Engagements ({engagements.length})
          </div>
          <div className="space-y-1">
            {engagements.map((e) => (
              <div key={e.id} className="flex items-center gap-3 p-2 bg-white border border-[var(--color-border)]/15 rounded text-sm">
                <span className="font-mono text-sm">#{e.id}</span>
                <span>{e.kind}</span>
                <span className="px-1.5 py-0.5 rounded text-sm font-medium bg-[var(--color-surface)]">{e.status}</span>
                <span className="font-mono text-sm ml-auto">{e.score}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
