import { Pill } from "../components/Pill";
import type { PillState } from "../lib/pill-text";
import type { LiveResult, LiveVerdict } from "../lib/live/guards";
import { ACTIVE_STATUSES } from "../lib/live/guards";
import { NOTICE_FALLBACK, NOTICES, STAGE_NAMES } from "../lib/live/messages";

const DECIDED_BY: Record<LiveVerdict["decided_by"], string> = {
  model: "the model",
  rule: "a rule on coded data",
  "rule+model": "a rule and the model",
};

/** A condition that does not hold reads "Not applicable", whatever outcome the server attached. */
function stateOf(v: LiveVerdict): Exclude<PillState, "false_approval" | "invalidated"> {
  return v.applicable ? v.outcome : "not_applicable";
}

function Verdict({ verdict }: { verdict: LiveVerdict }) {
  return (
    <li className="rounded-lg border border-line bg-surface p-3" data-testid="live-verdict" data-criterion={verdict.criterion}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="min-w-0 break-words font-mono text-sm font-semibold">{verdict.criterion}</h3>
        <Pill state={stateOf(verdict)} />
        <span className="text-xs text-ink-3">decided by {DECIDED_BY[verdict.decided_by]}</span>
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm">{verdict.rationale}</p>
      {verdict.citations.length > 0 ? (
        <ul className="mt-2 space-y-2" aria-label={`Citations for ${verdict.criterion}`}>
          {verdict.citations.map((c, i) => (
            <li key={`${c.chunk_id}-${c.char_start}-${i}`} className="border-l-4 border-line-strong pl-3 text-sm">
              <blockquote className="whitespace-pre-wrap break-words">{c.quote}</blockquote>
              <p className="mt-0.5 break-all font-mono text-xs text-ink-3">{c.chunk_id}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-ink-3">No citation.</p>
      )}
    </li>
  );
}

/**
 * The result of one live run. It shows what the run produced and the notices that go with it. It has
 * no expected labels, no right or wrong marks and no comparison with the recorded run, because the
 * server sends none and a live result is not evidence of accuracy.
 */
export function ResultView({ result, timings }: { result: LiveResult; timings: Record<string, number> }) {
  const counts = { met: 0, not_met: 0, insufficient: 0, not_applicable: 0 };
  for (const v of result.verdicts) counts[stateOf(v)] += 1;
  return (
    <section aria-labelledby="result-h" className="space-y-4" data-testid="live-result">
      <div className="rounded-lg border-2 border-line-strong bg-surface p-4">
        <h2 id="result-h" className="text-xl font-bold">
          Live result for <span className="font-mono">{result.case_id}</span>
        </h2>
        <p className="mt-2" data-testid="synthetic-notice">
          {NOTICES.synthetic}
        </p>
        <p className="mt-1 font-medium" data-testid="server-notice">
          {result.notice === "" ? NOTICE_FALLBACK : result.notice}
        </p>
        <p className="mt-1 text-sm text-ink-2">
          A person decides. Nothing on this page approves or denies a request.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Pill state={result.overall} prefix="Overall:" large />
        <span className="text-sm text-ink-2">
          {result.policy}, {result.variant}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5" data-testid="live-counts">
        <span className="mr-1 text-xs text-ink-3">{result.verdicts.length} criteria</span>
        <Pill state="met" count={counts.met} />
        <Pill state="not_met" count={counts.not_met} />
        <Pill state="insufficient" count={counts.insufficient} />
        {counts.not_applicable > 0 ? <Pill state="not_applicable" count={counts.not_applicable} /> : null}
      </div>

      <section aria-labelledby="verdicts-h">
        <h2 id="verdicts-h" className="text-lg font-semibold">
          Criteria
        </h2>
        <ul className="mt-2 space-y-3">
          {result.verdicts.map((v) => (
            <Verdict key={v.criterion} verdict={v} />
          ))}
        </ul>
      </section>

      {result.reviewer_checklist.length > 0 ? (
        <section aria-labelledby="checklist-h" data-testid="reviewer-checklist">
          <h2 id="checklist-h" className="text-lg font-semibold">
            Left for the human reviewer
          </h2>
          <p className="text-sm text-ink-2">These criteria cannot be settled from the chart.</p>
          <ul className="mt-2 space-y-2">
            {result.reviewer_checklist.map((item) => (
              <li key={item.criterion} className="rounded-lg border border-line bg-surface p-3 text-sm">
                <p className="break-words font-mono font-semibold">{item.criterion}</p>
                <p className="text-xs text-ink-3">needs: {item.evidence_stage.replace(/_/g, " ")}</p>
                <p className="mt-1 whitespace-pre-wrap break-words">{item.text}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="usage-h" data-testid="live-usage">
        <h2 id="usage-h" className="text-lg font-semibold">
          This run
        </h2>
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-ink-3">Input tokens</dt>
          <dd className="tabular-nums">{result.input_tokens.toLocaleString("en-US")}</dd>
          <dt className="text-ink-3">Output tokens</dt>
          <dd className="tabular-nums">{result.output_tokens.toLocaleString("en-US")}</dd>
          <dt className="text-ink-3">Judging latency</dt>
          <dd className="tabular-nums">{(result.latency_ms / 1000).toFixed(1)} s</dd>
          {ACTIVE_STATUSES.map((stage) =>
            timings[stage] === undefined ? null : (
              <div key={stage} className="contents" data-testid="stage-timing">
                <dt className="text-ink-3">{STAGE_NAMES[stage]}</dt>
                <dd className="tabular-nums">{timings[stage].toFixed(1)} s</dd>
              </div>
            ),
          )}
        </dl>
      </section>
    </section>
  );
}
