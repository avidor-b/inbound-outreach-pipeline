"use client";

import { useEffect, useRef } from "react";

interface ToolReasoningProps {
  name: string;
  args?: object | unknown;
  status: string;
}

const statusIndicator = {
  executing: (
    <span className="inline-block h-2 w-2 rounded-full bg-primary animate-pulse" />
  ),
  inProgress: (
    <span className="inline-block h-2 w-2 rounded-full bg-primary animate-pulse" />
  ),
  complete: <span className="text-primary text-[10px] font-bold">[OK]</span>,
};

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.length} items]`;
  if (typeof value === "object" && value !== null)
    return `{${Object.keys(value).length} keys}`;
  if (typeof value === "string")
    return value.length > 60 ? `"${value.slice(0, 57)}..."` : `"${value}"`;
  return String(value);
}

export function ToolReasoning({ name, args, status }: ToolReasoningProps) {
  const entries = args ? Object.entries(args) : [];
  const toolStatus = status as "complete" | "inProgress" | "executing";

  return (
    <div className="my-1.5 font-[family-name:var(--font-mono)] text-xs">
      <div className="flex items-start gap-2 pl-4 border-l-2 border-primary/30 relative">
        <span className="absolute -left-[5px] top-1 w-2 h-2 rounded-full bg-primary" />
        <div className="flex-1">
          <div className="flex items-center gap-2 text-text-muted">
            <span className="text-primary">&gt;</span>
            <span className="font-medium text-text">
              {name}
              {entries.length > 0 && (
                <span className="text-text-faint">
                  ({entries.map(([k, v]) => `${k}=${formatValue(v)}`).join(", ")})
                </span>
              )}
            </span>
            {statusIndicator[toolStatus]}
          </div>
        </div>
      </div>
    </div>
  );
}
