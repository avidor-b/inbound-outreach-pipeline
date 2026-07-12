"use client";

interface Column {
  key: string;
  label: string;
  width?: string;
  render?: (value: any, row: any) => React.ReactNode;
}

interface ToolTableProps {
  title: string;
  columns: Column[];
  rows: any[];
  count?: number;
  emptyMessage?: string;
  /** Short "what is this?" tooltip shown on the title's (i) hover. */
  helpText?: string;
}

const scoreBadge = (score: number) => {
  if (!score) return "bg-gray-100 text-gray-500";
  if (score >= 70) return "bg-emerald-100 text-emerald-700";
  if (score >= 50) return "bg-yellow-100 text-yellow-700";
  if (score >= 25) return "bg-orange-100 text-orange-600";
  return "bg-red-100 text-red-600";
};

const statusBadge = (status: string) => {
  const colors: Record<string, string> = {
    open: "bg-blue-100 text-blue-700",
    scoring: "bg-yellow-100 text-yellow-700",
    drafting: "bg-purple-100 text-purple-700",
    blocked: "bg-red-100 text-red-700",
    closed: "bg-gray-100 text-gray-600",
    sent: "bg-emerald-100 text-emerald-700",
    approved: "bg-emerald-100 text-emerald-700",
    draft: "bg-blue-100 text-blue-700",
    pending_approval: "bg-amber-100 text-amber-700",
    sdr_handoff: "bg-sky-100 text-sky-700",
  };
  return colors[status] || "bg-gray-100 text-gray-600";
};

export const RENDERERS = {
  score: (v: any) =>
    v ? (
      <span
        className={`inline-block px-1.5 py-0.5 rounded text-sm font-bold tabular-nums ${scoreBadge(Number(v))}`}
      >
        {v}
      </span>
    ) : (
      <span className="text-text-faint">--</span>
    ),
  status: (v: any) =>
    v ? (
      <span
        className={`inline-block px-1.5 py-0.5 rounded text-sm font-semibold uppercase ${statusBadge(String(v))}`}
      >
        {v}
      </span>
    ) : null,
  boolean: (v: any) => (
    <span className={v ? "text-emerald-600" : "text-text-faint"}>
      {v ? "Yes" : "No"}
    </span>
  ),
  truncate: (v: any) => {
    const s = String(v || "");
    return (
      <span className="truncate block max-w-[200px]" title={s}>
        {s}
      </span>
    );
  },
  id: (v: any) => <span className="font-mono text-sm">{v}</span>,
};

export function ToolTable({
  title,
  columns,
  rows,
  count,
  emptyMessage,
  helpText,
}: ToolTableProps) {
  if (!rows || rows.length === 0) {
    return (
      <div className="my-2 px-4 py-3 rounded-md border border-border/20 bg-surface text-sm text-text-muted">
        {emptyMessage || `No ${title.toLowerCase()} found.`}
      </div>
    );
  }

  return (
    <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
      <div className="px-4 py-2 bg-primary/5 border-b border-border/20 flex items-center justify-between">
        <span className="text-sm font-semibold uppercase tracking-wider text-primary flex items-center gap-1.5">
          {title}
          {helpText && (
            <span
              tabIndex={0}
              title={helpText}
              aria-label={helpText}
              className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary/15 text-primary text-[10px] font-bold leading-none cursor-help focus:outline focus:outline-2 focus:outline-primary/40"
            >
              i
            </span>
          )}
        </span>
        {count != null && (
          <span className="text-sm font-mono text-text-muted">
            {count} result{count !== 1 ? "s" : ""}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/10">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="px-3 py-2 text-left text-sm font-semibold uppercase tracking-wider text-text-faint"
                  style={col.width ? { width: col.width } : undefined}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.id || row.contact_id || row.engagement_id || i}
                className="border-b border-border/5 last:border-0 hover:bg-primary/3 transition-colors"
              >
                {columns.map((col) => (
                  <td key={col.key} className="px-3 py-2 text-text-muted">
                    {col.render
                      ? col.render(row[col.key], row)
                      : row[col.key] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
