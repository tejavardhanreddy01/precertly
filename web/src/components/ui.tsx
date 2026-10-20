import type { ReactNode } from "react";
import { OUTCOME_TEXT, type Shown } from "../lib/labels";

// Every outcome has its own colour pair (text on a light tint, both at least 4.5:1), its own
// shape and its own words, so colour is never the only signal.
const STYLE: Record<Shown, string> = {
  met: "border-green-800 bg-green-50 text-green-900",
  not_met: "border-red-800 bg-red-50 text-red-900",
  insufficient: "border-amber-800 bg-amber-50 text-amber-950",
  not_applicable: "border-slate-600 bg-slate-100 text-slate-900",
};

function Icon({ outcome }: { outcome: Shown }) {
  const common = { width: 14, height: 14, viewBox: "0 0 16 16", "aria-hidden": true, focusable: false } as const;
  switch (outcome) {
    case "met":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M4.5 8.3l2.4 2.4 4.6-5" fill="none" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      );
    case "not_met":
      return (
        <svg {...common}>
          <rect x="1.5" y="1.5" width="13" height="13" rx="2" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M5 5l6 6M11 5l-6 6" fill="none" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      );
    case "insufficient":
      return (
        <svg {...common}>
          <path d="M8 1.5L15 14H1z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          <path d="M8 6.2v3.6M8 11.6v.9" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
    case "not_applicable":
      return (
        <svg {...common}>
          <path d="M8 1.5l6.5 6.5L8 14.5 1.5 8z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          <path d="M5 8h6" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
  }
}

export function OutcomeBadge({ outcome, prefix }: { outcome: Shown; prefix?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-semibold ${STYLE[outcome]}`}
      data-outcome={outcome}
    >
      <Icon outcome={outcome} />
      <span>
        {prefix ? `${prefix} ` : ""}
        {OUTCOME_TEXT[outcome]}
      </span>
    </span>
  );
}

/** Whether a recorded result agrees with its label, in words and a shape. */
export function CorrectMark({ correct, what = "" }: { correct: boolean; what?: string }) {
  const text = `${correct ? "Correct" : "Incorrect"}${what ? ` ${what}` : ""}`;
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-xs font-semibold ${
        correct ? "text-green-900" : "text-red-900"
      }`}
      data-correct={correct}
    >
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        {correct ? (
          <path d="M3 8.5l3.2 3.2L13 4.5" fill="none" stroke="currentColor" strokeWidth="2" />
        ) : (
          <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" strokeWidth="2" />
        )}
      </svg>
      {text}
    </span>
  );
}

export function FalseApprovalMarker({ children }: { children: ReactNode }) {
  return (
    <p
      className="flex items-start gap-2 rounded border-2 border-red-900 bg-red-100 p-2 text-sm font-bold text-red-950"
      data-false-approval
    >
      <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true" focusable="false" className="mt-0.5 shrink-0">
        <path d="M8 1l7 13H1z" fill="currentColor" />
        <path d="M8 6v4M8 11.5v1" stroke="#fff" strokeWidth="1.6" />
      </svg>
      <span>
        <span className="uppercase tracking-wide">False approval. </span>
        {children}
      </span>
    </p>
  );
}

export function LabelsOnlyBadge() {
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded border border-slate-600 bg-white px-1.5 py-0.5 text-xs font-semibold text-slate-900">
      labels only, not yet scored
    </span>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className="text-slate-600">{children}</span>;
}
