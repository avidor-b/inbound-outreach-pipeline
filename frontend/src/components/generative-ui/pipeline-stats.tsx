"use client";

import { z } from "zod";

export const PipelineStatsProps = z.object({
  contacts: z.object({
    total: z.number(),
    enriched: z.number(),
    scored: z.number(),
  }),
  submissions: z.object({
    total: z.number(),
    by_intent: z.record(z.number()).optional(),
  }),
  engagements: z.object({
    total: z.number(),
    by_status: z.record(z.number()).optional(),
    by_kind: z.record(z.number()).optional(),
    score_distribution: z.object({
      high: z.number(),
      medium: z.number(),
      low: z.number(),
      unscored: z.number(),
    }).optional(),
  }),
  drafts: z.object({
    total: z.number(),
    by_status: z.record(z.number()).optional(),
  }),
  outreach: z.object({
    sent: z.number(),
    sends_today: z.number(),
  }).optional(),
  replies: z.object({
    total: z.number(),
    hot_leads: z.number(),
  }).optional(),
  opt_outs: z.number().optional(),
});

type PipelineStatsProps = z.infer<typeof PipelineStatsProps>;

function StatCard({ label, value, sub }: { label: string; value: number | string; sub?: string }) {
  return (
    <div className="p-4 bg-white border border-[var(--color-border)]/20 rounded">
      <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-1">
        {label}
      </div>
      <div className="text-2xl font-[family-name:var(--font-headline)] font-bold text-[var(--color-text)]">
        {value}
      </div>
      {sub && <div className="text-sm text-[var(--color-text-muted)] mt-0.5">{sub}</div>}
    </div>
  );
}

export function PipelineStats(props: Record<string, unknown>) {
  const result = PipelineStatsProps.safeParse(props);
  if (!result.success) {
    return <div className="my-3 p-4 text-sm text-[var(--color-text-muted)] animate-pulse">Loading pipeline stats...</div>;
  }
  const { contacts, engagements, submissions, drafts, outreach, replies, opt_outs } = result.data;
  return (
    <div className="my-3 max-w-2xl">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard label="Contacts" value={contacts.total} sub={`${contacts.enriched} enriched, ${contacts.scored} scored`} />
        <StatCard label="Engagements" value={engagements.total} />
        <StatCard label="Submissions" value={submissions.total} />
        <StatCard label="Drafts" value={drafts.total} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {engagements.by_status && Object.keys(engagements.by_status).length > 0 && (
          <div className="p-4 bg-white border border-[var(--color-border)]/20 rounded">
            <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
              By Status
            </div>
            <div className="space-y-1">
              {Object.entries(engagements.by_status).map(([status, count]) => (
                <div key={status} className="flex justify-between text-sm">
                  <span className="text-[var(--color-text-muted)]">{status}</span>
                  <span className="font-medium">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {engagements.by_kind && Object.keys(engagements.by_kind).length > 0 && (
          <div className="p-4 bg-white border border-[var(--color-border)]/20 rounded">
            <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
              By Kind
            </div>
            <div className="space-y-1">
              {Object.entries(engagements.by_kind).map(([kind, count]) => (
                <div key={kind} className="flex justify-between text-sm">
                  <span className="text-[var(--color-text-muted)]">{kind}</span>
                  <span className="font-medium">{count}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {engagements.score_distribution && (
        <div className="mt-3 p-4 bg-white border border-[var(--color-border)]/20 rounded">
          <div className="font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)] mb-2">
            Score Distribution
          </div>
          <div className="flex gap-4 text-sm">
            <span>High: <strong>{engagements.score_distribution.high}</strong></span>
            <span>Medium: <strong>{engagements.score_distribution.medium}</strong></span>
            <span>Low: <strong>{engagements.score_distribution.low}</strong></span>
            <span className="text-[var(--color-text-muted)]">Unscored: {engagements.score_distribution.unscored}</span>
          </div>
        </div>
      )}
      {(replies?.hot_leads ?? 0) > 0 && (
        <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded text-sm">
          <span className="font-bold text-red-700">{replies!.hot_leads} hot leads</span>
          <span className="text-red-600"> need attention</span>
        </div>
      )}
    </div>
  );
}
