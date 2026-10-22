import { useEffect, useRef, type MouseEvent } from "react";
import { ErrorScreen, Loading } from "../components/ErrorScreen";
import { useLoaded, useStore } from "../lib/data";
import { useIndex } from "../lib/index-context";
import { stageTitle } from "../lib/labels";
import { kindOf, ownCriteria, sharedCriteria, type LogicItem } from "../lib/policyLogic";
import { reveal } from "../lib/reveal";
import { href, parseHash, type Route } from "../lib/router";
import { useTitle } from "../lib/title";
import type { Logic, PolicyCriterion, PolicyFile } from "../lib/types";

const VARIANTS_ID = "variants-h";

function criterionElement(id: string): Element | undefined {
  return [...document.querySelectorAll("[data-criterion]")].find((el) => el instanceof HTMLElement && el.dataset.criterion === id);
}

/** Source links come from the data; only https links are rendered as anchors. */
function safeUrl(url: string): string | undefined {
  return url.startsWith("https://") ? url : undefined;
}

export function PolicyIndex() {
  const { policies, cases } = useIndex();
  useTitle("Policies");
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Policies</h1>
      <p className="mt-1 max-w-text text-ink-2">
        Each policy is encoded from the governing Medicare coverage text as atomic criteria with a verbatim quote,
        plus the logic that combines them for each kind of request.
      </p>
      <ul className="mt-4 grid gap-2 md:grid-cols-2">
        {policies.map((p) => (
          <li key={p.id} className="rounded-lg border border-line bg-surface p-4">
            <h2 className="font-semibold">
              <a className="text-accent underline underline-offset-2" href={href(["policies", p.id])}>
                {p.title}
              </a>
            </h2>
            <p className="text-sm text-ink-3">
              {p.document} · {cases.filter((c) => c.policy === p.id).length} cases
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A link to a criterion card on this page. Following a link whose target is already the address
 * changes no hash, so nothing would re-render or scroll: that click scrolls the card directly.
 */
function CriterionLink({ id, policyId, criteria }: { id: string; policyId: string; criteria: Map<string, PolicyCriterion> }) {
  const target = href(["policies", policyId], { c: id });
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    const now = parseHash(window.location.hash);
    if (now.path.join("/") === `policies/${policyId}` && now.query.get("c") === id) {
      event.preventDefault();
      reveal(criterionElement(id));
    }
  };
  return (
    <>
      <a className="text-accent underline underline-offset-2" href={target} onClick={onClick}>
        {criteria.get(id)?.text ?? id}
      </a>{" "}
      <span className="break-all font-mono text-xs text-ink-3">({id})</span>
    </>
  );
}

type Shared = { policyId: string; criteria: Map<string, PolicyCriterion> };

function ItemList({ items, policyId, criteria }: { items: LogicItem[] } & Shared) {
  return (
    <ul className="ml-4 list-disc space-y-1">
      {items.map((item, i) =>
        typeof item === "string" ? (
          <li key={item}>
            <CriterionLink id={item} policyId={policyId} criteria={criteria} />
          </li>
        ) : (
          <li key={i} className="list-none">
            <LogicList logic={item} policyId={policyId} criteria={criteria} />
          </li>
        ),
      )}
    </ul>
  );
}

function LogicList({ logic, policyId, criteria }: { logic: Logic } & Shared) {
  const kind = kindOf(logic) === "all_of" ? "All of" : "Any of";
  return (
    <div>
      <p className="font-semibold">{kind}:</p>
      <ItemList items={logic.all_of ?? logic.any_of ?? []} policyId={policyId} criteria={criteria} />
    </div>
  );
}

function CriterionCard({ c, policy, selected }: { c: PolicyCriterion; policy: PolicyFile; selected: boolean }) {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (selected) reveal(ref.current);
  }, [selected]);
  const source = policy.sources.find((s) => s.document === c.source_document);
  return (
    <li
      ref={ref}
      data-criterion={c.id}
      className={`rounded-lg border bg-surface p-4 ${selected ? "border-2 border-accent" : "border-line"}`}
    >
      <h3 className="font-semibold">{c.text}</h3>
      <p className="break-all font-mono text-xs text-ink-3">{c.id}</p>
      <p className="mt-1 text-sm">
        <span className="rounded-full border border-line-strong bg-sunken px-2 py-0.5 text-xs font-semibold text-ink-2">
          Stage: {stageTitle(c.evidence_stage)}
        </span>{" "}
        <span className="text-ink-3">
          {c.kind} · {c.severity}
        </span>
      </p>
      {c.condition ? (
        <p className="mt-2 text-sm">
          <span className="font-semibold">Applies only when: </span>
          {c.condition}
          {c.condition_evidence ? <span className="text-ink-3"> (shown by: {c.condition_evidence})</span> : null}
        </p>
      ) : null}
      <blockquote className="mt-2 whitespace-pre-wrap border-l-4 border-accent pl-3 text-sm text-ink-2">{c.quote}</blockquote>
      <p className="mt-1 text-xs text-ink-2">
        {c.source_document}, {c.source_section}
        {source ? (
          <>
            {" · "}
            <a className="text-accent underline underline-offset-2" href={safeUrl(source.url)}>
              {source.title}
            </a>
          </>
        ) : null}
      </p>
      {c.note ? <p className="mt-1 text-sm text-ink-2">Note: {c.note}</p> : null}
      <p className="mt-2 text-sm">
        <button
          type="button"
          className="text-accent underline underline-offset-2"
          onClick={() => reveal(document.getElementById(VARIANTS_ID))}
        >
          Back to variants
        </button>
      </p>
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
  const shared = sharedCriteria(policy.variants, policy.criteria.map((c) => c.id));
  return (
    <article className="space-y-6">
      <header>
        <p className="text-sm">
          <a className="text-accent underline underline-offset-2" href="#/policies">
            Policies
          </a>
        </p>
        <h1 className="text-2xl font-bold tracking-tight">{policy.title}</h1>
        <p className="text-ink-2">
          {policy.document} · quotes last checked against the source on {policy.verified_on}
        </p>
        <ul className="mt-2 text-sm">
          {policy.sources.map((s) => (
            <li key={s.url}>
              <a className="text-accent underline underline-offset-2" href={safeUrl(s.url)}>
                {s.document}: {s.title}
              </a>{" "}
              <span className="text-ink-3">
                (version {s.version}, effective {s.effective_date}
                {s.contractor ? `, ${s.contractor}` : ""})
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 max-w-text text-sm text-ink-2">
          The copilot judges only criteria at the chart and request stages. The other stages go on the reviewer
          checklist for a person.
        </p>
      </header>

      <section aria-labelledby={VARIANTS_ID}>
        <h2 id={VARIANTS_ID} className="text-lg font-semibold">
          Variants and their logic
        </h2>
        <p className="text-sm text-ink-2">
          Which criteria must hold depends on the kind of request. This logic is code, not model output; a criterion
          that does not apply counts as satisfied.
        </p>
        {shared.length > 0 ? (
          <>
            <p className="mt-1 text-sm text-ink-2">
              The criteria under “Required for every procedure” apply to each procedure below, in addition to the
              ones listed for that procedure.
            </p>
            <div data-testid="shared-criteria" className="mt-2 rounded-lg border border-line bg-surface p-4 text-sm">
              <h3 className="font-semibold">Required for every procedure</h3>
              <ItemList items={shared} policyId={policy.id} criteria={byId} />
            </div>
          </>
        ) : null}
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          {policy.variants.map((v) => {
            const own = ownCriteria(v, shared);
            return (
              <div key={v.id} data-variant={v.id} className="rounded-lg border border-line bg-surface p-4 text-sm">
                <h3 className="font-semibold">{v.title}</h3>
                <p className="break-all font-mono text-xs text-ink-3">
                  {v.id}
                  {v.codes.length > 0 ? ` · codes ${v.codes.join(", ")}` : ""}
                </p>
                <div className="mt-2" data-own-criteria>
                  {shared.length > 0 ? <h4 className="font-semibold">Specific to this procedure</h4> : null}
                  {own.length > 0 ? (
                    <ItemList items={own} policyId={policy.id} criteria={byId} />
                  ) : (
                    <p className="text-ink-2">
                      {shared.length > 0 ? "Nothing beyond the criteria required for every procedure." : "No criteria."}
                    </p>
                  )}
                </div>
                <details className="mt-3">
                  <summary className="cursor-pointer font-semibold text-accent">Show exact logic</summary>
                  <div className="mt-2">
                    <LogicList logic={v.logic} policyId={policy.id} criteria={byId} />
                  </div>
                </details>
              </div>
            );
          })}
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
