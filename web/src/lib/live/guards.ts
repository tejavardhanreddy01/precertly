// Runtime guards for everything the live API sends. A response is untrusted input: each guard
// returns the value in a known shape or null, and a null becomes the fixed "unexpected response"
// failure. Unknown fields are ignored, never copied. Nothing here builds markup.

export const ACTIVE_STATUSES = ["waking_database", "checking_limits", "ingesting", "judging"] as const;
export const FINISHED_STATUSES = ["done", "failed", "rejected"] as const;
export const RUN_STATUSES = [...ACTIVE_STATUSES, ...FINISHED_STATUSES] as const;
export const OUTCOMES = ["met", "not_met", "insufficient"] as const;
export const DECIDED_BY = ["model", "rule", "rule+model"] as const;

export type ActiveStatus = (typeof ACTIVE_STATUSES)[number];
export type FinishedStatus = (typeof FINISHED_STATUSES)[number];
export type RunStatus = (typeof RUN_STATUSES)[number];
export type Outcome = (typeof OUTCOMES)[number];
export type DecidedBy = (typeof DECIDED_BY)[number];

export interface LiveCitation {
  chunk_id: string;
  quote: string;
  char_start: number;
  char_end: number;
}

export interface LiveVerdict {
  criterion: string;
  outcome: Outcome;
  applicable: boolean;
  decided_by: DecidedBy;
  confidence: number;
  rationale: string;
  citations: LiveCitation[];
}

export interface LiveChecklistItem {
  criterion: string;
  evidence_stage: string;
  text: string;
}

export interface LiveResult {
  case_id: string;
  policy: string;
  variant: string;
  overall: Outcome;
  verdicts: LiveVerdict[];
  reviewer_checklist: LiveChecklistItem[];
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  notice: string;
}

export interface Started {
  run_id: string;
  status: RunStatus;
}

export interface RunState {
  run_id: string;
  case_id: string;
  status: RunStatus;
  reason: string | null;
  message: string | null;
  started_at: string;
  finished_at: string | null;
  timings: Record<string, number>;
  result: LiveResult | null;
}

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isCount = (v: unknown): v is number => isNum(v) && Number.isInteger(v) && v >= 0;
const oneOf = <T extends string>(list: readonly T[], v: unknown): v is T => isStr(v) && (list as readonly string[]).includes(v);

function listOf<T>(v: unknown, guard: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(v)) return null;
  const out: T[] = [];
  for (const item of v) {
    const parsed = guard(item);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

export function parseCitation(v: unknown): LiveCitation | null {
  if (!isObj(v)) return null;
  const { chunk_id, quote, char_start, char_end } = v;
  if (!isStr(chunk_id) || !isStr(quote) || !isCount(char_start) || !isCount(char_end)) return null;
  return { chunk_id, quote, char_start, char_end };
}

export function parseVerdict(v: unknown): LiveVerdict | null {
  if (!isObj(v)) return null;
  const { criterion, outcome, applicable, decided_by, confidence, rationale, citations } = v;
  if (
    !isStr(criterion) ||
    !oneOf(OUTCOMES, outcome) ||
    typeof applicable !== "boolean" ||
    !oneOf(DECIDED_BY, decided_by) ||
    !isNum(confidence) ||
    !isStr(rationale)
  ) {
    return null;
  }
  const cites = listOf(citations, parseCitation);
  if (cites === null) return null;
  return { criterion, outcome, applicable, decided_by, confidence, rationale, citations: cites };
}

function parseChecklistItem(v: unknown): LiveChecklistItem | null {
  if (!isObj(v)) return null;
  const { criterion, evidence_stage, text } = v;
  if (!isStr(criterion) || !isStr(evidence_stage) || !isStr(text)) return null;
  return { criterion, evidence_stage, text };
}

export function parseResult(v: unknown): LiveResult | null {
  if (!isObj(v)) return null;
  const { case_id, policy, variant, overall, verdicts, reviewer_checklist, input_tokens, output_tokens, latency_ms, notice } = v;
  if (
    !isStr(case_id) ||
    !isStr(policy) ||
    !isStr(variant) ||
    !oneOf(OUTCOMES, overall) ||
    !isCount(input_tokens) ||
    !isCount(output_tokens) ||
    !isNum(latency_ms) ||
    !isStr(notice)
  ) {
    return null;
  }
  const parsedVerdicts = listOf(verdicts, parseVerdict);
  const checklist = listOf(reviewer_checklist, parseChecklistItem);
  if (parsedVerdicts === null || checklist === null) return null;
  return {
    case_id,
    policy,
    variant,
    overall,
    verdicts: parsedVerdicts,
    reviewer_checklist: checklist,
    input_tokens,
    output_tokens,
    latency_ms,
    notice,
  };
}

export function parseStarted(v: unknown): Started | null {
  if (!isObj(v)) return null;
  const { run_id, status } = v;
  if (!isStr(run_id) || run_id === "" || !oneOf(RUN_STATUSES, status)) return null;
  return { run_id, status };
}

function parseTimings(v: unknown): Record<string, number> | null {
  if (!isObj(v)) return null;
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(v)) {
    if (!isNum(value) || value < 0) return null;
    out[key] = value;
  }
  return out;
}

export function parseRunState(v: unknown): RunState | null {
  if (!isObj(v)) return null;
  const { run_id, case_id, status, reason, message, started_at, finished_at, timings, result } = v;
  if (!isStr(run_id) || !isStr(case_id) || !oneOf(RUN_STATUSES, status) || !isStr(started_at)) return null;
  if (reason !== null && reason !== undefined && !isStr(reason)) return null;
  if (message !== null && message !== undefined && !isStr(message)) return null;
  if (finished_at !== null && finished_at !== undefined && !isStr(finished_at)) return null;
  const parsedTimings = parseTimings(timings);
  if (parsedTimings === null) return null;
  let parsedResult: LiveResult | null = null;
  if (result !== null && result !== undefined) {
    parsedResult = parseResult(result);
    if (parsedResult === null) return null;
  }
  // A finished run carries its result and nothing else does.
  if ((status === "done") !== (parsedResult !== null)) return null;
  return {
    run_id,
    case_id,
    status,
    reason: reason ?? null,
    message: message ?? null,
    started_at,
    finished_at: finished_at ?? null,
    timings: parsedTimings,
    result: parsedResult,
  };
}

export const isFinished = (status: RunStatus): status is FinishedStatus => oneOf(FINISHED_STATUSES, status);
