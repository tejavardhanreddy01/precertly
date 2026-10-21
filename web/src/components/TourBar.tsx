import { TOUR, tourHref, tourUnseenHref } from "../content/tour";

/** Shown above a case when the address carries ?tour=N. */
export function TourBar({ step }: { step: number }) {
  const current = TOUR[step - 1];
  if (!current) return null;
  const unseenHref = tourUnseenHref(step);
  const pill = "rounded border border-blue-900 px-2 py-1 text-sm font-medium text-blue-950 hover:bg-blue-100";
  return (
    <nav aria-label="Tour" className="rounded border-2 border-blue-900 bg-blue-50 p-3 text-blue-950">
      <p className="text-xs font-semibold uppercase tracking-wide">
        Tour, step {step} of {TOUR.length}
      </p>
      <p className="mt-1 font-semibold">{current.title}</p>
      <p className="text-sm">{current.caption}</p>
      <p className="mt-2 flex flex-wrap gap-2">
        {unseenHref && current.unseen ? (
          <a className={pill} href={unseenHref}>
            {current.unseen.label}
          </a>
        ) : null}
        {step > 1 ? (
          <a className={pill} href={tourHref(step - 1)}>
            Previous step
          </a>
        ) : null}
        {step < TOUR.length ? (
          <a className={`${pill} bg-blue-900 !text-white hover:!bg-blue-800`} href={tourHref(step + 1)}>
            Next step
          </a>
        ) : null}
        <a className={pill} href="#/cases">
          {step < TOUR.length ? "Leave the tour" : "Finish and browse the cases"}
        </a>
      </p>
    </nav>
  );
}
