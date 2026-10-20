import { useEffect, useRef } from "react";
import { ErrorScreen, Loading } from "../components/ErrorScreen";
import { useLoaded, useStore } from "../lib/data";
import { useIndex } from "../lib/index-context";
import { stageTitle } from "../lib/labels";
import { href, type Route } from "../lib/router";
import { useTitle } from "../lib/title";
import type { Logic, PolicyCriterion, PolicyFile } from "../lib/types";

/** Source links come from the data; only https links are rendered as anchors. */
function safeUrl(url: string): string | undefined {
  return url.startsWith("https://") ? url : undefined;
}

export function PolicyIndex() {
  const { policies, cases } = useIndex();
  useTitle("Policies");
  return (
    <div>
      <h1 className="text-2xl font-bold">Policies</h1>
      <p className="mt-1 max-w-3xl text-slate-800">
        Each policy is encoded from the governing Medicare coverage text as atomic criteria with a verbatim quote,
        plus the logic that combines them for each kind of request.
      </p>
      <ul className="mt-4 grid gap-2 md:grid-cols-2">
        {policies.map((p) => (
          <li key={p.id} className="rounded border border-slate-300 bg-white p-3">
            <h2 className="font-semibold">
              <a className="underline" href={href(["policies", p.id])}>
                {p.title}
              </a>
            </h2>
            <p className="text-sm text-slate-700">
              {p.document} · {cases.filter((c) => c.policy === p.id).length} cases
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function LogicList({
  logic,
  policyId,
  criteria,
}: {
  logic: Logic;
  policyId: string;
  criteria: Map<string, PolicyCriterion>;
}) {
  const [kind, items] = logic.all_of ? (["All of", logic.all_of] as const) : (["Any of", logic.any_of ?? []] as const);
  return (
    <div>
      <p className="font-semibold">{kind}:</p>
      <ul className="ml-4 list-disc space-y-1">
        {items.map((item, i) =>
          typeof item === "string" ? (
            <li key={item}>
              <a className="underline" href={href(["policies", policyId], { c: item })}>
                {criteria.get(item)?.text ?? item}
              </a>{" "}
              <span className="break-all text-xs text-slate-700">({item})</span>
            </li>
          ) : (
            <li key={i} className="list-none">
              <LogicList logic={item} policyId={policyId} criteria={criteria} />
            </li>
          ),
        )}
      </ul>
    </div>
  );
}

function CriterionCard({ c, policy, selected }: { c: PolicyCriterion; policy: PolicyFile; selected: boolean }) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView?.({ block: "center" });
  }, [selected]);
  const source = policy.sources.find((s) => s.document === c.source_document);
  return (
    <li
      ref={ref}
      data-criterion={c.id}
      className={`rounded border bg-white p-3 ${selected ? "border-2 border-slate-900" : "border-slate-300"}`}
    >
      <h3 className="font-semibold">{c.text}</h3>
      <p className="break-all text-xs text-slate-700">{c.id}</p>
      <p className="mt-1 text-sm">
        <span className="rounded border border-slate-600 bg-slate-100 px-1.5 py-0.5 text-xs font-semibold">
          Stage: {stageTitle(c.evidence_stage)}
        </span>{" "}
        <span className="text-slate-700">
          {c.kind} · {c.severity}
        </span>
      </p>
      {c.condition ? (
        <p className="mt-2 text-sm">
          <span className="font-semibold">Applies only when: </span>
          {c.condition}
          {c.condition_evidence ? <span className="text-slate-700"> (shown by: {c.condition_evidence})</span> : null}
        </p>
      ) : null}
      <blockquote className="mt-2 whitespace-pre-wrap border-l-4 border-slate-400 pl-3 text-sm">{c.quote}</blockquote>
      <p className="mt-1 text-xs text-slate-800">
        {c.source_document}, {c.source_section}
        {source ? (
          <>
            {" · "}
            <a className="underline" href={safeUrl(source.url)}>
              {source.title}
            </a>
          </>
        ) : null}
      </p>
      {c.note ? <p className="mt-1 text-sm text-slate-800">Note: {c.note}</p> : null}
    </li>
  );
}

export function PolicyScreen({ id, route }: { id: string; route: Route }) {
  const store = useStore();
  const { policies } = useIndex();
  const known = policies.some((p) => p.id === id);
  const loaded = useLoaded(`policy:${id}`, () => store.policy(id));
  useTitle(known ? `Policy ${id}` : "Policy not found");

  if (!known) return <ErrorScreen title="Policy not found" message={`There is no policy called "${id}".`} />;
  if (loaded.status === "loading") return <Loading what={`policy ${id}`} />;
  if (loaded.status === "error") {
    return <ErrorScreen title={`Policy ${id} could not be loaded`} message={loaded.message} />;
  }
  const policy = loaded.value;
  const byId = new Map(policy.criteria.map((c) => [c.id, c]));
  const selected = route.query.get("c");
  return (
    <article className="space-y-6">
      <header>
        <p className="text-sm">
          <a className="underline" href="#/policies">
            Policies
          </a>
        </p>
        <h1 className="text-2xl font-bold">{policy.title}</h1>
        <p className="text-slate-800">
          {policy.document} · quotes last checked against the source on {policy.verified_on}
        </p>
        <ul className="mt-2 text-sm">
          {policy.sources.map((s) => (
            <li key={s.url}>
              <a className="underline" href={safeUrl(s.url)}>
                {s.document}: {s.title}
              </a>{" "}
              <span className="text-slate-700">
                (version {s.version}, effective {s.effective_date}
                {s.contractor ? `, ${s.contractor}` : ""})
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 max-w-3xl text-sm text-slate-800">
          The copilot judges only criteria at the chart and request stages. The other stages go on the reviewer
          checklist for a person.
        </p>
      </header>

      <section aria-labelledby="variants-h">
        <h2 id="variants-h" className="text-lg font-semibold">
          Variants and their logic
        </h2>
        <p className="text-sm text-slate-800">
          Which criteria must hold depends on the kind of request. This logic is code, not model output; a criterion
          that does not apply counts as satisfied.
        </p>
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          {policy.variants.map((v) => (
            <div key={v.id} className="rounded border border-slate-300 bg-white p-3 text-sm">
              <h3 className="font-semibold">{v.title}</h3>
              <p className="break-all text-xs text-slate-700">
                {v.id}
                {v.codes.length > 0 ? ` · codes ${v.codes.join(", ")}` : ""}
              </p>
              <div className="mt-2">
                <LogicList logic={v.logic} policyId={policy.id} criteria={byId} />
              </div>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="criteria-h">
        <h2 id="criteria-h" className="text-lg font-semibold">
          Criteria ({policy.criteria.length})
        </h2>
        <ul className="mt-2 grid gap-3 lg:grid-cols-2">
          {policy.criteria.map((c) => (
            <CriterionCard key={c.id} c={c} policy={policy} selected={c.id === selected} />
          ))}
        </ul>
      </section>

      {policy.not_covered.length > 0 || policy.notes.length > 0 ? (
        <section aria-labelledby="notes-h">
          <h2 id="notes-h" className="text-lg font-semibold">
            Notes
          </h2>
          {policy.not_covered.length > 0 ? (
            <>
              <h3 className="mt-2 font-semibold">Not covered by this encoding</h3>
              <ul className="list-inside list-disc text-sm">
                {policy.not_covered.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </>
          ) : null}
          {policy.notes.length > 0 ? (
            <>
              <h3 className="mt-2 font-semibold">Encoding notes</h3>
              <ul className="list-inside list-disc text-sm">
                {policy.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}
