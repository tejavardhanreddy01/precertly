import { humanize } from "../lib/labels";
import type { Request } from "../lib/types";

function show(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? "none" : value.join(", ");
  return String(value);
}

/** The request as recorded in the case file; every value is rendered as text. */
export function RequestPanel({ request }: { request: Request }) {
  const entries = Object.entries(request).filter(([, v]) => v !== null && v !== undefined);
  return (
    <section aria-labelledby="request-h" className="rounded border border-slate-300 bg-white p-3">
      <h2 id="request-h" className="text-base font-semibold">
        Request
      </h2>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-slate-700">{humanize(key)}</dt>
            <dd className="break-words font-medium">{show(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
