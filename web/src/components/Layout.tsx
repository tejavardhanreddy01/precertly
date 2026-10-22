import type { ReactNode } from "react";
import { isKnownRoute, parseHash, useHash } from "../lib/router";
import { IconCode } from "./icons";
import { RunChip } from "./RunChip";

export const REPO_URL = "https://github.com/tejavardhanreddy01/precertly";

const NAV: { label: string; path: string; href: string }[] = [
  { label: "Cases", path: "cases", href: "#/cases" },
  { label: "Policies", path: "policies", href: "#/policies" },
  { label: "About", path: "about", href: "#/about" },
];

function TopBar({ withRun }: { withRun: boolean }) {
  const path = parseHash(useHash()).path;
  // An address with no page (#/nope, #/policies/a/b) highlights nothing.
  const current = isKnownRoute(path) ? path[0] : undefined;
  return (
    <header className="sticky top-0 z-30 h-(--size-topbar) shrink-0 border-b border-line bg-surface">
      <div className="mx-auto flex h-full max-w-page items-center gap-2 px-3 sm:gap-4 sm:px-6">
        <a href="#/" className="mr-1 text-lg font-bold tracking-tight text-ink">
          Precertly
        </a>
        {withRun ? (
          <nav aria-label="Main" className="flex items-center gap-0.5">
            {NAV.map((item) => (
              <a
                key={item.path}
                href={item.href}
                aria-current={current === item.path ? "page" : undefined}
                className={`rounded-md px-2 py-1.5 text-sm font-medium sm:px-3 ${
                  current === item.path ? "bg-accent-soft text-accent-strong" : "text-ink-2 hover:bg-sunken"
                }`}
              >
                {item.label}
              </a>
            ))}
          </nav>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {withRun ? <RunChip /> : null}
          <a
            href={REPO_URL}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-ink-2 hover:bg-sunken"
          >
            <IconCode size={16} />
            <span className="max-sm:sr-only">Repository</span>
          </a>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-10 border-t border-line bg-surface">
      <div className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-2 px-4 py-4 text-sm text-ink-2 sm:px-6">
        <p>Recorded runs only. Nothing runs when you open this site.</p>
        <a className="text-accent underline underline-offset-2" href={REPO_URL}>
          github.com/tejavardhanreddy01/precertly
        </a>
      </div>
    </footer>
  );
}

/**
 * The application shell: a top bar that stays in view and the content area. A workspace page (a
 * case) fills the viewport exactly and never scrolls itself; its panes scroll. Every other page
 * scrolls normally under the bar, in a column of readable width.
 */
export function Layout({
  children,
  ready = true,
  workspace = false,
}: {
  children: ReactNode;
  /** False while index.json is loading or failed: the bar then has no nav or run chip. */
  ready?: boolean;
  workspace?: boolean;
}) {
  return (
    <div className={workspace ? "flex h-dvh flex-col overflow-hidden" : "flex min-h-dvh flex-col"}>
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface focus:p-2 focus:text-ink"
      >
        Skip to the content
      </a>
      <TopBar withRun={ready} />
      <main
        id="main"
        tabIndex={-1}
        className={
          workspace
            ? "min-h-0 flex-1 overflow-y-auto focus:outline-none"
            : "mx-auto w-full max-w-page flex-1 px-4 py-6 focus:outline-none sm:px-6 sm:py-8"
        }
      >
        {children}
      </main>
      {workspace ? null : <Footer />}
    </div>
  );
}
