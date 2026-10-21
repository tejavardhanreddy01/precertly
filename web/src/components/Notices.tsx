import { useIndex } from "../lib/index-context";
import { shortHash } from "../lib/labels";
import { IconInfo } from "./icons";

/** The full notices and the recorded-run label. Shown on the landing page and About; the top bar
 * carries the short form on every other screen. */
export function Notices() {
  const { banners, run } = useIndex();
  return (
    <section aria-label="Notices" className="rounded-lg border border-line bg-surface p-4">
      <ul className="space-y-1.5 text-sm text-ink-2">
        {banners.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <IconInfo size={14} className="mt-1 shrink-0 text-ink-3" />
            <span>{b}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 border-t border-line pt-3 text-sm font-medium" data-testid="run-label">
        Recorded run {run.run_date} · prompt version {run.prompt_version} · system prompt hash{" "}
        <span className="font-mono text-xs">{shortHash(run.system_prompt_sha256)}</span> · git{" "}
        <span className="font-mono text-xs">{run.git_sha}</span> · model{" "}
        <span className="font-mono text-xs">{run.model_id}</span>
      </p>
    </section>
  );
}
