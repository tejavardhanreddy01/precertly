import { GateMark } from "../components/GateMark";
import { IconArrowRight } from "../components/icons";
import { Notices } from "../components/Notices";
import { StatTiles } from "../components/StatTiles";
import {
  DOCS_QUALITY_URL,
  GATES,
  GATES_CAVEAT,
  LIMITATIONS,
  NOTES_URL,
  PIPELINE,
  WHAT_IT_IS,
  WHAT_IT_IS_NOT,
} from "../content/about";
import { tourHref } from "../content/tour";
import { useIndex } from "../lib/index-context";
import { useTitle } from "../lib/title";

const textLink = "font-medium text-accent underline underline-offset-2";

export function About() {
  const { summary } = useIndex();
  useTitle("About and method");
  return (
    <div className="mx-auto max-w-text space-y-8">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">About and method</h1>
        <p className="mt-3">
          <a
            href={tourHref(1)}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-on-accent hover:bg-accent-strong"
          >
            Take the 4-step tour
            <IconArrowRight size={14} />
          </a>
        </p>
      </header>

      <Notices />

      <article className="space-y-8">
        <section aria-labelledby="is-h" className="space-y-2">
          <h2 id="is-h" className="text-lg font-semibold">
            What this is, and what it is not
          </h2>
          {WHAT_IT_IS.map((p) => (
            <p key={p}>{p}</p>
          ))}
          <ul className="list-inside list-disc space-y-1">
            {WHAT_IT_IS_NOT.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="pipe-h">
          <h2 id="pipe-h" className="text-lg font-semibold">
            How a result is made, in five steps
          </h2>
          <ol className="mt-2 list-inside list-decimal space-y-2">
            {PIPELINE.map((step) => (
              <li key={step.title}>
                <span className="font-semibold">{step.title}.</span> {step.text}
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby="run-h">
          <h2 id="run-h" className="text-lg font-semibold">
            The recorded run
          </h2>
          <p className="font-semibold">Facet cases only.</p>
          <p className="mb-3 text-sm text-ink-2">
            These numbers come from the data file this page loaded. The other 18 cases have not been scored.
          </p>
          <StatTiles summary={summary} id="run-figures" />
        </section>

        <section aria-labelledby="gates-h">
          <h2 id="gates-h" className="text-lg font-semibold">
            Release gates
          </h2>
          <p className="text-sm text-ink-2">{GATES_CAVEAT}</p>
          <p className="text-sm text-ink-2">
            Thresholds are starting goals, published with the real numbers, including the failures. The table mirrors{" "}
            <a className={textLink} href={DOCS_QUALITY_URL}>
              docs/QUALITY.md
            </a>
            .
          </p>
          <div
            className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface"
            tabIndex={0}
            role="region"
            aria-label="Release gates table, scrolls sideways on a narrow screen"
          >
            <table className="w-full min-w-[40rem] border-collapse text-left text-sm" data-testid="gates">
              <caption className="sr-only">Release gates, their thresholds and the current results on the facet cases</caption>
              <thead>
                <tr className="border-b border-line-strong bg-sunken">
                  <th scope="col" className="p-2 font-semibold">
                    Gate
                  </th>
                  <th scope="col" className="p-2 font-semibold">
                    Threshold
                  </th>
                  <th scope="col" className="p-2 font-semibold">
                    Current (facet cases only)
                  </th>
                  <th scope="col" className="p-2 font-semibold">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {GATES.map((g) => (
                  <tr key={g.gate} className="border-b border-line align-top last:border-b-0">
                    <th scope="row" className="p-2 font-semibold">
                      {g.gate}
                    </th>
                    <td className="p-2">{g.threshold}</td>
                    <td className="p-2">{g.current}</td>
                    <td className="p-2">
                      <GateMark status={g.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="lim-h">
          <h2 id="lim-h" className="text-lg font-semibold">
            Known limitations
          </h2>
          <ul className="mt-2 space-y-2">
            {LIMITATIONS.map((l) => (
              <li key={l.title} className="rounded-lg border border-line bg-surface p-3">
                <h3 className="font-semibold">{l.title}</h3>
                <p className="text-sm text-ink-2">{l.text}</p>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-sm">
            The full list is in{" "}
            <a className={textLink} href={DOCS_QUALITY_URL}>
              docs/QUALITY.md
            </a>{" "}
            and the experiment record in{" "}
            <a className={textLink} href={NOTES_URL}>
              evals/results/NOTES.md
            </a>
            .
          </p>
        </section>
      </article>
    </div>
  );
}
