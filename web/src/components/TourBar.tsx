import { TOUR, tourHref, tourUnseenHref } from "../content/tour";
import { IconArrowRight, IconEyeOff } from "./icons";

const button =
  "inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-sunken";

/** Pinned to the bottom of the case workspace when the address carries ?tour=N. */
export function TourBar({ step, onShowEvidence }: { step: number; onShowEvidence?: () => void }) {
  const current = TOUR[step - 1];
  if (!current) return null;
  const unseenHref = tourUnseenHref(step);
  return (
    <nav
      aria-label="Tour"
      className="z-20 shrink-0 border-t-2 border-accent bg-accent-soft px-3 py-2 shadow-bar sm:px-5"
    >
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="min-w-0 flex-1 basis-80">
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className="rounded-full bg-accent px-2 py-0.5 text-xs font-semibold text-on-accent">
              Tour, step {step} of {TOUR.length}
            </span>
            <span className="font-semibold text-ink">{current.title}</span>
          </p>
          <details className="lg:hidden">
            <summary className="mt-1 cursor-pointer text-sm font-medium text-accent-strong">What to notice</summary>
            <p className="mt-1 text-sm text-ink-2">{current.caption}</p>
          </details>
          <p className="mt-1 max-w-4xl text-sm text-ink-2 max-lg:hidden">{current.caption}</p>
        </div>
        <p className="flex flex-wrap items-center gap-2">
          {unseenHref && current.unseen ? (
            <a className={button} href={unseenHref} onClick={onShowEvidence}>
              <IconEyeOff size={14} />
              {current.unseen.label}
            </a>
          ) : null}
          {step > 1 ? (
            <a className={button} href={tourHref(step - 1)}>
              Previous step
            </a>
          ) : null}
          {step < TOUR.length ? (
            <a
              className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-on-accent hover:bg-accent-strong"
              href={tourHref(step + 1)}
            >
              Next step
              <IconArrowRight size={14} />
            </a>
          ) : null}
          <a className={button} href="#/cases">
            {step < TOUR.length ? "Leave the tour" : "Finish and browse the cases"}
          </a>
        </p>
      </div>
    </nav>
  );
}
