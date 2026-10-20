// Types come from public/data/schema.json (the export's Pydantic models) through
// `npm run types`; nothing here is written by hand except the re-exports.
export type {
  CaseRow,
  Index,
  PolicyRef,
  RunInfo,
  RunSummary,
} from "../generated/index";
export type {
  Logic,
  PolicyCriterion,
  PolicyFile,
  PolicySource,
  PolicyVariant,
} from "../generated/policy";
export type {
  CaseCriterion,
  CaseFile,
  ChartChunk,
  Citation,
  ComputedFact,
  ExpectedLabel,
  RecordedResult,
  Request,
  Verdict,
} from "../generated/case";

/** The schema_version of index.json this UI was built for. */
export const SUPPORTED_SCHEMA_VERSION = 1;

export type Outcome = "met" | "not_met" | "insufficient";
