"use client";

import { z } from "zod";

const EngagementRow = z.object({
  id: z.number(),
  kind: z.string(),
  status: z.string(),
  score: z.number(),
  primary_product: z.string().optional(),
  contact_id: z.string(),
  company: z.string().optional(),
});

export const EngagementTableProps = z.object({
  engagements: z.array(EngagementRow).describe("List of engagements to display"),
  title: z.string().optional().describe("Table title"),
});

type EngagementTableProps = z.infer<typeof EngagementTableProps>;

const statusColors: Record<string, string> = {
  open: "bg-blue-100 text-blue-700",
  scoring: "bg-yellow-100 text-yellow-700",
  drafting: "bg-purple-100 text-purple-700",
  pending_approval: "bg-orange-100 text-orange-700",
  approved: "bg-green-100 text-green-700",
  sent: "bg-emerald-100 text-emerald-800",
  blocked: "bg-red-100 text-red-700",
  closed: "bg-gray-100 text-gray-600",
};

export function EngagementTable(props: Record<string, unknown>) {
  const result = EngagementTableProps.safeParse(props);
  if (!result.success) {
    return <div className="my-3 p-4 text-sm text-[var(--color-text-muted)] animate-pulse">Loading engagements...</div>;
  }
  const { engagements, title } = result.data;
  if (engagements.length === 0) {
    return <div className="my-3 p-4 text-sm text-[var(--color-text-muted)]">No engagements found</div>;
  }
  return (
    <div className="my-3 max-w-3xl overflow-x-auto">
      {title && (
        <div className="font-[family-name:var(--font-headline)] font-bold text-sm mb-2">{title}</div>
      )}
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-[var(--color-border)]/30">
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">ID</th>
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">Kind</th>
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">Status</th>
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">Score</th>
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">Product</th>
            <th className="text-left py-2 px-3 font-[family-name:var(--font-label)] text-sm uppercase tracking-widest text-[var(--color-text-muted)]">Contact</th>
          </tr>
        </thead>
        <tbody>
          {engagements.map((e) => (
            <tr key={e.id} className="border-b border-[var(--color-border)]/10 hover:bg-[var(--color-surface)]">
              <td className="py-2 px-3 font-mono text-sm">#{e.id}</td>
              <td className="py-2 px-3">{e.kind}</td>
              <td className="py-2 px-3">
                <span className={`inline-block px-2 py-0.5 rounded text-sm font-medium ${statusColors[e.status] || "bg-gray-100"}`}>
                  {e.status}
                </span>
              </td>
              <td className="py-2 px-3 font-[family-name:var(--font-mono)]">{e.score}</td>
              <td className="py-2 px-3 text-[var(--color-text-muted)]">{e.primary_product || "-"}</td>
              <td className="py-2 px-3 text-sm text-[var(--color-text-muted)]">{e.company || e.contact_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-sm text-[var(--color-text-faint)] mt-1">{engagements.length} engagements</div>
    </div>
  );
}
