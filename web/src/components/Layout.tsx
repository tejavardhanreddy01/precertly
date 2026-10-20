import type { ReactNode } from "react";
import { tourHref } from "../content/tour";
import { useIndex } from "../lib/index-context";
import { shortHash } from "../lib/labels";

export const REPO_URL = "https://github.com/tejavardhanreddy01/precertly";

const link =
  "rounded px-2 py-1 text-sm font-medium text-slate-900 underline-offset-4 hover:underline";

/** The notices and the recorded-run label: shown on every screen and never collapsible. */
export function NoticeStrip() {
  const { banners, run } = useIndex();
  return (
    <section aria-label="Notices" className="border-b border-slate-300 bg-amber-50 text-xs text-slate-900">
      <div className="mx-auto max-w-7xl px-4 py-2">
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {banners.map((b) => (
            <li key={b} className="flex items-start gap-1">
              <span aria-hidden="true">•</span>
              <span>{b}</span>
            </li>
          ))}
        </ul>
        <p className="mt-1 font-semibold" data-testid="run-label">
          Recorded run {run.run_date} · prompt version {run.prompt_version} · system prompt hash{" "}
          <span className="font-mono">{shortHash(run.system_prompt_sha256)}</span> · git{" "}
          <span className="font-mono">{run.git_sha}</span> · model <span className="font-mono">{run.model_id}</span>
        </p>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="mt-8 border-t border-slate-300 bg-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-sm text-slate-900">
        <p>Recorded runs only. Nothing runs when you open this site.</p>
        <a className="underline" href={REPO_URL}>
          github.com/tejavardhanreddy01/precertly
        </a>
      </div>
    </footer>
  );
}

export function Layout({ children, withNotices = true }: { children: ReactNode; withNotices?: boolean }) {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:p-2 focus:text-slate-900"
      >
        Skip to the content
      </a>
      <header className="border-b border-slate-300 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-2">
          <p className="text-lg font-bold">Precertly <span className="font-normal text-slate-700">recorded runs</span></p>
          {withNotices ? (
            <nav aria-label="Main" className="flex flex-wrap items-center gap-1">
              <a className={link} href="#/cases">
                Cases
              </a>
              <a className={link} href="#/policies">
                Policies
              </a>
              <a className={link} href="#/about">
                About and method
              </a>
              <a
                className="rounded border border-slate-900 bg-slate-900 px-2 py-1 text-sm font-semibold text-white hover:bg-slate-700"
                href={tourHref(1)}
              >
                Take the 4-step tour
              </a>
            </nav>
          ) : null}
        </div>
      </header>
      {withNotices ? <NoticeStrip /> : null}
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-4 focus:outline-none">
        {children}
      </main>
      <Footer />
    </div>
  );
}
