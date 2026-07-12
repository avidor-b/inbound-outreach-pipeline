"use client";

interface StatItem {
  label: string;
  value: string | number;
  color?: string;
  /** Optional sub-line under the value (e.g. "3 approved, 0 pending"). */
  hint?: string;
}

interface ToolSummaryProps {
  title: string;
  stats: StatItem[];
  helpText?: string;
}

export function ToolSummary({ title, stats, helpText }: ToolSummaryProps) {
  if (!stats || stats.length === 0) return null;

  return (
    <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
      <div className="px-4 py-2 bg-primary/5 border-b border-border/20">
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
      </div>
      <div className="px-4 py-3 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
        {stats.map((s, i) => (
          <div key={i} className="text-center">
            <div
              className={`text-xl font-bold tabular-nums ${s.color || "text-primary"}`}
            >
              {s.value}
            </div>
            <div className="text-sm text-text-faint uppercase tracking-wider mt-0.5">
              {s.label}
            </div>
            {s.hint && (
              <div className="text-sm text-text-faint mt-0.5">{s.hint}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
