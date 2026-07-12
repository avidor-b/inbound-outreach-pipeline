"use client";

interface Section {
  title: string;
  fields: { label: string; value: any; render?: (v: any) => React.ReactNode; wide?: boolean }[];
}

interface ToolDetailProps {
  title: string;
  subtitle?: string;
  sections: Section[];
}

export function ToolDetail({ title, subtitle, sections }: ToolDetailProps) {
  return (
    <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
      <div className="px-4 py-2.5 bg-primary/5 border-b border-border/20">
        <span className="text-sm font-semibold uppercase tracking-wider text-primary">
          {title}
        </span>
        {subtitle && (
          <span className="ml-2 text-sm font-mono text-text-muted">
            {subtitle}
          </span>
        )}
      </div>
      {sections.map((sec, si) => (
        <div
          key={si}
          className={`px-4 py-3 ${si < sections.length - 1 ? "border-b border-border/10" : ""}`}
        >
          <p className="text-sm font-semibold uppercase tracking-wider text-text-faint mb-2">
            {sec.title}
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {sec.fields
              .filter((f) => f.value !== "" && f.value != null)
              .map((f, fi) => (
                <div key={fi} className={f.wide ? "col-span-2" : ""}>
                  <span className="text-text-faint">{f.label}</span>
                  <p className={`font-medium ${f.wide ? "whitespace-pre-wrap" : "truncate"}`}>
                    {f.render ? f.render(f.value) : String(f.value)}
                  </p>
                </div>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}
