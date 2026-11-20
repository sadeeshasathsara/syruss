"use client";

import { ChevronDown, ChevronUp, AlertTriangle, CheckCircle2, XCircle, Info, Eye, Calculator, ClipboardList, Database } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Pill } from "@/components/ui/Pill";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn, percent } from "@/lib/format";
import type { ConfidenceDetails } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

interface Props {
  confidenceDetails?: ConfidenceDetails;
  overallConfidence: number;
  className?: string;
  defaultExpanded?: boolean;
}

export function ConfidenceScorecard({
  confidenceDetails,
  overallConfidence,
  className,
  defaultExpanded = false,
}: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const { href } = useWorkspace();

  const conf = confidenceDetails?.total_confidence ?? overallConfidence;
  const isVeto = !!confidenceDetails?.hard_cap_applied;

  const tone = isVeto || conf < 0.7 ? "negative" : conf < 0.9 ? "warning" : "positive";

  const breakdown = confidenceDetails?.breakdown;

  return (
    <div className={cn("rounded-card border border-border bg-card transition-all", className)}>
      {/* Pill Header / Trigger */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between gap-3 p-3 text-left hover:bg-sheet/50 transition-colors rounded-card focus:outline-none focus:ring-1 focus:ring-control"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-2.5">
          <Pill tone={tone} className="px-2 py-0.5 font-semibold text-meta">
            {percent(conf)} Confidence
          </Pill>
          {isVeto && (
            <span className="flex items-center gap-1 text-meta text-[var(--pill-negative-ink)] font-medium">
              <AlertTriangle className="h-3.5 w-3.5" />
              Hard-Cap Veto
            </span>
          )}
          <span className="text-meta text-muted hidden @md:inline">
            {confidenceDetails ? `${confidenceDetails.total_pts.toFixed(0)} / 100 points` : "Overall confidence score"}
          </span>
        </div>

        <div className="flex items-center gap-1 text-meta text-muted">
          <span>{expanded ? "Hide scorecard" : "View scorecard"}</span>
          {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </div>
      </button>

      {/* Expanded Breakdown */}
      {expanded && (
        <div className="border-t border-border p-4 flex flex-col gap-4 animate-rise text-body">
          {/* Veto Alert Banner */}
          {isVeto && (
            <div className="rounded-control border border-[var(--pill-negative-ink)]/30 bg-[var(--pill-negative-bg)]/20 p-3 flex flex-col gap-1.5 text-negative">
              <div className="flex items-center gap-2 font-semibold text-meta text-[var(--pill-negative-ink)]">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>HARD-CAP VETO APPLIED: Final confidence capped at 50%</span>
              </div>
              <ul className="list-disc pl-5 text-meta space-y-0.5 text-ink">
                {confidenceDetails.veto_messages.map((msg, i) => (
                  <li key={i}>{msg}</li>
                ))}
              </ul>
            </div>
          )}

          {/* 4 Fixed Dimensions Grid */}
          <div className="grid grid-cols-1 @md:grid-cols-2 gap-3.5">
            {/* 1. Visual Reading */}
            <div className="rounded-control border border-border p-3 flex flex-col gap-2 bg-sheet/40">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-meta font-medium text-ink">
                  <Eye className="h-3.5 w-3.5 text-muted" />
                  Visual & Field Integrity
                </span>
                <span className="font-semibold text-meta text-ink">
                  {breakdown ? `${breakdown.visual.score.toFixed(1)} / ${breakdown.visual.max}` : `${(conf * 40).toFixed(1)} / 40`} pts
                </span>
              </div>
              <ProgressBar
                current={breakdown ? breakdown.visual.score : conf * 40}
                max={40}
                tone={breakdown && breakdown.visual.missing_critical_fields?.length > 0 ? "negative" : "accent"}
              />
              <p className="text-meta text-muted">
                {breakdown?.visual.missing_critical_fields?.length ? (
                  <span className="text-negative">
                    Missing critical fields: {breakdown.visual.missing_critical_fields.join(", ")}
                  </span>
                ) : (
                  "Image legibility and critical fields validated"
                )}
              </p>
            </div>

            {/* 2. Math & Formats */}
            <div className="rounded-control border border-border p-3 flex flex-col gap-2 bg-sheet/40">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-meta font-medium text-ink">
                  <Calculator className="h-3.5 w-3.5 text-muted" />
                  Formats & Arithmetic
                </span>
                <span className="font-semibold text-meta text-ink">
                  {breakdown ? `${breakdown.math_format.score.toFixed(1)} / ${breakdown.math_format.max}` : "— / 20"} pts
                </span>
              </div>
              <ProgressBar
                current={breakdown ? breakdown.math_format.score : 0}
                max={20}
                tone={breakdown?.math_format.has_discrepancy ? "negative" : "accent"}
              />
              <p className="text-meta text-muted">
                {breakdown ? (
                  breakdown.math_format.has_discrepancy ? (
                    <span className="text-negative">Arithmetic discrepancy detected</span>
                  ) : breakdown.math_format.arithmetic_checks > 0 ? (
                    `${breakdown.math_format.arithmetic_passed}/${breakdown.math_format.arithmetic_checks} math checks reconciled`
                  ) : (
                    "Checksums and standard formats verified"
                  )
                ) : (
                  "Checksums & arithmetic reconciler"
                )}
              </p>
            </div>

            {/* 3. Business Rules */}
            <div className="rounded-control border border-border p-3 flex flex-col gap-2 bg-sheet/40">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-meta font-medium text-ink">
                  <ClipboardList className="h-3.5 w-3.5 text-muted" />
                  Workspace Rules
                </span>
                <span className="font-semibold text-meta text-ink">
                  {breakdown ? `${breakdown.rules.score.toFixed(1)} / ${breakdown.rules.max}` : "0.0 / 20"} pts
                </span>
              </div>
              <ProgressBar
                current={breakdown ? breakdown.rules.score : 0}
                max={20}
                tone={breakdown?.rules.error_failures ? "negative" : "accent"}
              />
              <p className="text-meta text-muted">
                {breakdown?.rules.configured ? (
                  `${breakdown.rules.passed}/${breakdown.rules.total_active} active rules satisfied`
                ) : (
                  <span>
                    0 pts configured.{" "}
                    <Link href={href("settings/template")} className="underline text-ink hover:text-accent">
                      Add rules
                    </Link>{" "}
                    to raise confidence.
                  </span>
                )}
              </p>
            </div>

            {/* 4. Master Data Sources */}
            <div className="rounded-control border border-border p-3 flex flex-col gap-2 bg-sheet/40">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-meta font-medium text-ink">
                  <Database className="h-3.5 w-3.5 text-muted" />
                  Master Data Sources
                </span>
                <span className="font-semibold text-meta text-ink">
                  {breakdown ? `${breakdown.data_sources.score.toFixed(1)} / ${breakdown.data_sources.max}` : "0.0 / 20"} pts
                </span>
              </div>
              <ProgressBar
                current={breakdown ? breakdown.data_sources.score : 0}
                max={20}
                tone={breakdown?.data_sources.identity_collisions ? "negative" : "accent"}
              />
              <p className="text-meta text-muted">
                {breakdown?.data_sources.connected ? (
                  `${breakdown.data_sources.exact_matches} exact / ${breakdown.data_sources.alias_matches} alias matches`
                ) : (
                  <span>
                    0 pts connected.{" "}
                    <Link href={href("knowledge")} className="underline text-ink hover:text-accent">
                      Connect data sources
                    </Link>{" "}
                    to raise confidence.
                  </span>
                )}
              </p>
            </div>
          </div>

          <p className="text-meta text-muted">
            Scores use fixed 100-point weights: 40 visual, 20 math, 20 rules, 20 data sources. Unconnected sources or rules never shift points to other pillars.
          </p>
        </div>
      )}
    </div>
  );
}

function ProgressBar({ current, max, tone = "accent" }: { current: number; max: number; tone?: "accent" | "negative" }) {
  const pct = Math.min(100, Math.max(0, (current / max) * 100));
  const fillClass = tone === "negative" ? "bg-[var(--pill-negative-ink)]" : "bg-accent";
  return (
    <div className="h-1.5 w-full rounded-full bg-border overflow-hidden">
      <div className={cn("h-full transition-all duration-300 rounded-full", fillClass)} style={{ width: `${pct}%` }} />
    </div>
  );
}
