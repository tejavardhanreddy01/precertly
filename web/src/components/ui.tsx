import type { ReactNode } from "react";
import { IconAlert, IconClose, IconTick } from "./icons";

/** Whether a recorded result agrees with its label, in words and a shape. */
export function CorrectMark({ correct, what = "" }: { correct: boolean; what?: string }) {
  const text = `${correct ? "Correct" : "Incorrect"}${what ? ` ${what}` : ""}`;
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap text-xs font-semibold ${
        correct ? "text-met-fg" : "text-notmet-fg"
      }`}
      data-correct={correct}
    >
      {correct ? <IconTick size={14} /> : <IconClose size={14} />}
      {text}
    </span>
  );
}

/** The strongest block on the site: a recorded approval the label does not support. */
export function FalseApprovalMarker({ children }: { children: ReactNode }) {
  return (
    <p
      className="flex items-start gap-2 rounded-md border-2 border-danger-deep bg-danger p-3 text-sm font-semibold text-on-danger"
      data-false-approval
    >
      <IconAlert size={20} className="mt-0.5 shrink-0" />
      <span>
        <span className="font-bold">False approval. </span>
        {children}
      </span>
    </p>
  );
}

export function LabelsOnlyBadge() {
  return (
    <span className="inline-flex items-center rounded-full border border-line-strong bg-surface px-2 py-0.5 text-xs font-medium text-ink-2 max-sm:rounded-lg max-sm:leading-snug sm:whitespace-nowrap">
      labels only, not yet scored
    </span>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className="text-ink-3">{children}</span>;
}
