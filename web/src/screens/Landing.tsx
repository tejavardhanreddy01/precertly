import { EvidenceMotif, IconArrowRight } from "../components/icons";
import { Notices } from "../components/Notices";
import { StatTiles } from "../components/StatTiles";
import { tourHref } from "../content/tour";
import { useIndex } from "../lib/index-context";
import { href } from "../lib/router";
import { useTitle } from "../lib/title";

export function Landing() {
  const { summary, cases } = useIndex();
  useTitle("Precertly");
  const scored = cases.filter((c) => c.scored).length;
  return (
    <div className="space-y-12">
      <section
        aria-labelledby="hero-h"
        className="flex items-center justify-between gap-8 rounded-lg border border-line bg-band px-5 py-8 sm:px-10 sm:py-9"
      >
        <div className="min-w-0">
          <p className="max-w-3xl text-xs font-semibold uppercase tracking-eyebrow text-accent-strong">
            Prior-authorization criteria checked against synthetic charts, with cited evidence
          </p>
          <h1 id="hero-h" className="mt-3 max-w-3xl text-3xl font-bold tracking-tight sm:text-4xl">
            Every verdict, backed by a quote from the chart.
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-ink-2">
            Precertly checks a policy against a synthetic chart. A person decides.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a
              href={tourHref(1)}
              className="inline-flex items-center gap-2 rounded-md bg-accent px-5 py-2.5 font-semibold text-on-accent hover:bg-accent-strong"
            >
              Start the 4-step tour
              <IconArrowRight size={16} />
            </a>
            <a
              href={href(["cases"])}
              className="inline-flex items-center rounded-md border border-line-strong bg-surface px-5 py-2.5 font-semibold text-ink hover:bg-sunken"
            >
              Browse the cases
            </a>
          </div>
        </div>
        <EvidenceMotif className="hidden shrink-0 sm:block" />
      </section>

      <section aria-labelledby="tiles-h">
        <h2 id="tiles-h" className="text-lg font-heading tracking-heading">
          The recorded run
        </h2>
        <p className="text-sm text-ink-3">
          Scored on {summary.cases} facet cases; the other {cases.length - scored} show labels only.
        </p>
        <div className="mt-3">
          <StatTiles summary={summary} wide />
        </div>
      </section>

      <section aria-labelledby="notices-h" className="max-w-text space-y-3">
        <h2 id="notices-h" className="text-lg font-heading tracking-heading">
          What to know before you read the numbers
        </h2>
        <Notices />
        <p className="text-sm">
          <a className="font-medium text-accent underline underline-offset-2" href={href(["about"])}>
            About and method
          </a>
          {" · "}
          <a className="font-medium text-accent underline underline-offset-2" href={href(["policies"])}>
            Policies
          </a>
        </p>
      </section>
    </div>
  );
}
