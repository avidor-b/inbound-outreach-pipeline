"use client";

const scoreColor = (score: number) => {
  if (score >= 90) return "text-emerald-600";
  if (score >= 70) return "text-green-600";
  if (score >= 50) return "text-yellow-600";
  if (score >= 25) return "text-orange-500";
  return "text-red-500";
};

const scoreBg = (score: number) => {
  if (score >= 90) return "bg-emerald-500";
  if (score >= 70) return "bg-green-500";
  if (score >= 50) return "bg-yellow-500";
  if (score >= 25) return "bg-orange-400";
  return "bg-red-400";
};

const dimBg = (score: number) => {
  if (score >= 8) return "bg-emerald-500";
  if (score >= 6) return "bg-green-500";
  if (score >= 4) return "bg-yellow-500";
  if (score >= 2) return "bg-orange-400";
  return "bg-red-400";
};

const statusColors: Record<string, string> = {
  drafting: "bg-purple-100 text-purple-700",
  scoring: "bg-yellow-100 text-yellow-700",
  closed: "bg-gray-100 text-gray-600",
  blocked: "bg-red-100 text-red-700",
  open: "bg-blue-100 text-blue-700",
};

interface Dimension {
  name: string;
  score: number;
  evidence?: string;
}

function parseDimensions(reasoning: string): { dimensions: Dimension[]; evidence: string } {
  // Format:
  //   "Product:8/10 | Intent:7/10 | ...\n• Product: ...\n• Intent: ...\n"
  // (fallback: older "...Evidence here" single-line format)
  const [firstLine, ...rest] = reasoning.split(/\n/);
  const stripped = firstLine.replace(/\.$/, "");
  const bulletMatches = rest
    .map((l) => l.trim())
    .filter((l) => l.startsWith("•"))
    .map((l) => l.replace(/^•\s*/, ""));
  const evidenceByDim: Record<string, string> = {};
  for (const line of bulletMatches) {
    const m = line.match(/^(\w+):\s*(.+)$/);
    if (m) evidenceByDim[m[1].toLowerCase()] = m[2].trim();
  }

  const dimensions: Dimension[] = [];
  for (const segment of stripped.split("|")) {
    const m = segment.trim().match(/(\w+):(\d+)\/10/);
    if (m) {
      const name = m[1];
      dimensions.push({
        name,
        score: parseInt(m[2]),
        evidence: evidenceByDim[name.toLowerCase()],
      });
    }
  }

  // Legacy single-line fallback: no newline → everything after first
  // `.` is the evidence string.
  let fallbackEvidence = "";
  if (dimensions.length === 0 || (!bulletMatches.length && rest.length === 0)) {
    const legacy = reasoning.match(/^([\w:/|0-9\s]+)\.\s*(.*)$/);
    if (legacy) {
      fallbackEvidence = legacy[2] || "";
      if (dimensions.length === 0) {
        for (const segment of legacy[1].split("|")) {
          const m = segment.trim().match(/(\w+):(\d+)\/10/);
          if (m) dimensions.push({ name: m[1], score: parseInt(m[2]) });
        }
      }
    }
  }

  return { dimensions, evidence: fallbackEvidence };
}

interface ScoreResultProps {
  engagement_id: number;
  score: number;
  score_reasoning?: string;
  primary_product?: string;
  status?: string;
  contact_id?: string;
  company?: string;
  jobtitle?: string;
  country?: string;
  region?: string;
  industry?: string;
  contact_message?: string;
  already_scored?: boolean;
  skipped?: boolean;
  reason?: string;
  error?: string;
}

export function ScoreResult(props: ScoreResultProps) {
  const {
    engagement_id,
    score,
    score_reasoning,
    primary_product,
    status,
    company,
    jobtitle,
    country,
    region,
    industry,
    contact_message,
    already_scored,
    skipped,
    error,
    reason,
  } = props;

  const metaParts = [
    jobtitle,
    industry,
    [country, region].filter(Boolean).join(" · "),
  ].filter(Boolean);

  if (error) {
    return (
      <div className="my-2 px-4 py-2.5 rounded-md border border-red-500/30 bg-red-50/5 text-sm text-red-400">
        {error}
      </div>
    );
  }

  if (skipped) {
    const isBlocked = reason?.includes("blocked");
    const blockMatch = reason?.match(/blocked \((\w+)\): (.+)/);
    return (
      <div className={`my-2 px-4 py-2.5 rounded-md border ${isBlocked ? "border-orange-500/30 bg-orange-50/5" : "border-border/20 bg-surface"}`}>
        <div className="flex items-center gap-2 flex-wrap">
          {company && (
            <span className="text-sm font-semibold text-text">{company}</span>
          )}
          <span className="font-mono text-sm text-text-faint">#{engagement_id}</span>
          {blockMatch ? (
            <span className="px-2 py-0.5 rounded text-sm font-semibold uppercase bg-orange-100 text-orange-700">
              {blockMatch[1].replace("_", " ")}
            </span>
          ) : (
            <span className="text-sm text-text-muted">skipped</span>
          )}
        </div>
        {(industry || region) && (
          <p className="text-sm text-text-faint mt-0.5">
            {[industry, region].filter(Boolean).join(" · ")}
          </p>
        )}
        {blockMatch && (
          <p className="text-sm text-text-muted mt-1">{blockMatch[2]}</p>
        )}
        {!blockMatch && reason && (
          <p className="text-sm text-text-muted mt-1">{reason}</p>
        )}
        {contact_message && (
          <blockquote className="mt-1.5 border-l-4 border-primary/40 bg-primary/5 pl-3 pr-3 py-2 text-sm text-text-muted italic whitespace-pre-wrap">
            {contact_message}
          </blockquote>
        )}
      </div>
    );
  }

  const barWidth = Math.max(score, 3);
  const { dimensions, evidence } = parseDimensions(score_reasoning || "");

  return (
    <div className="my-2 rounded-md border border-border/30 bg-surface overflow-hidden">
      <div className="px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={`text-2xl font-bold tabular-nums ${scoreColor(score)}`}>
            {score}
          </span>
          <div>
            {company && (
              <div className="text-sm font-semibold text-text">{company}</div>
            )}
            {metaParts.length > 0 && (
              <div className="text-sm text-text-muted">{metaParts.join(" — ")}</div>
            )}
            <div className="text-sm text-text-faint">
              Engagement <span className="font-mono">#{engagement_id}</span>
              {already_scored && (
                <span className="ml-2 text-text-faint italic">(already scored)</span>
              )}
            </div>
            {primary_product && primary_product !== "general" && (
              <span className="text-sm font-medium text-primary">{primary_product}</span>
            )}
          </div>
        </div>
        {status && (
          <span
            className={`px-2 py-0.5 rounded text-sm font-semibold uppercase ${statusColors[status] || "bg-gray-100 text-gray-600"}`}
          >
            {status}
          </span>
        )}
      </div>

      {/* Overall score bar */}
      <div className="px-4 pb-1">
        <div className="h-1.5 rounded-full bg-border/20 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${scoreBg(score)}`}
            style={{ width: `${barWidth}%` }}
          />
        </div>
      </div>

      {/* Dimension breakdown */}
      {dimensions.length > 0 && (
        <div className="px-4 py-2 grid grid-cols-5 gap-1.5">
          {dimensions.map((d) => (
            <div key={d.name} className="text-center" title={d.evidence}>
              <div className="text-sm text-text-faint uppercase tracking-wider">{d.name}</div>
              <div className="mt-1 h-1 rounded-full bg-border/20 overflow-hidden">
                <div
                  className={`h-full rounded-full ${dimBg(d.score)}`}
                  style={{ width: `${d.score * 10}%` }}
                />
              </div>
              <div className="text-sm font-mono text-text-muted mt-0.5">{d.score}/10</div>
            </div>
          ))}
        </div>
      )}

      {/* Per-dimension evidence — one line each, so the user sees
          WHY each score landed where it did instead of just the top one. */}
      {dimensions.some((d) => d.evidence) && (
        <ul className="px-4 pb-3 space-y-1 text-sm text-text-muted">
          {dimensions.filter((d) => d.evidence).map((d) => (
            <li key={d.name}>
              <span className="font-semibold text-text">{d.name}:</span> {d.evidence}
            </li>
          ))}
        </ul>
      )}

      {evidence && !dimensions.some((d) => d.evidence) && (
        <div className="px-4 pb-3 text-sm text-text-muted">{evidence}</div>
      )}
      {!dimensions.length && score_reasoning && (
        <div className="px-4 pb-3 text-sm text-text-muted whitespace-pre-wrap">
          {score_reasoning}
        </div>
      )}
    </div>
  );
}
