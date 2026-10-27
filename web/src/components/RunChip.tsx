import { useEffect, useId, useRef, useState } from "react";
import { useIndex } from "../lib/index-context";
import { useLiveEnabled } from "../lib/live-feature";
import { isLiveRoute, parseHash, useHash } from "../lib/router";
import { shortHash } from "../lib/labels";
import { IconInfo } from "./icons";

/** The recorded run in one line, for the case summary bar. */
export function RunStamp() {
  const { run } = useIndex();
  return (
    <p className="text-xs text-ink-3" data-testid="run-stamp">
      Recorded run {run.run_date} · prompt v{run.prompt_version} · hash{" "}
      <span className="font-mono">{shortHash(run.system_prompt_sha256)}</span>
    </p>
  );
}

/**
 * The one-line notice in the top bar: what the site shows. It opens a panel with the recorded run;
 * Escape or a click elsewhere closes it, and the full notices are on the landing page and About.
 */
export function RunChip() {
  const { run } = useIndex();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const onLivePage = isLiveRoute(parseHash(useHash()).path, useLiveEnabled());

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const rows: [string, string, boolean][] = [
    ["Recorded run", run.run_date, false],
    ["Prompt version", run.prompt_version, false],
    ["System prompt hash", shortHash(run.system_prompt_sha256), true],
    ["Git", run.git_sha, true],
    ["Model", run.model_id, true],
  ];

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-strong bg-surface text-xs max-sm:w-8 max-sm:justify-center sm:px-2.5 font-medium text-ink-2 hover:bg-sunken"
      >
        <IconInfo size={14} />
        <span className="max-sm:sr-only">{onLivePage ? "Synthetic data only" : "Recorded runs on synthetic data"}</span>
      </button>
      {open ? (
        <div
          id={panelId}
          role="group"
          aria-label="About the recorded run"
          className="fixed inset-x-4 top-[calc(var(--size-topbar)+0.5rem)] z-40 rounded-lg border border-line bg-surface p-4 shadow-pop sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96"
        >
          <p className="font-semibold">Recorded runs on synthetic data</p>
          <p className="mt-1 text-sm text-ink-2">
            Nothing runs when you open this site. Every patient in it is synthetic.
          </p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid="run-details">
            {rows.map(([k, v, mono]) => (
              <div key={k} className="contents">
                <dt className="text-ink-3">{k}</dt>
                <dd className={`min-w-0 break-all ${mono ? "font-mono text-xs leading-5" : ""}`}>{v}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-sm">
            <a
              className="font-medium text-accent underline underline-offset-2"
              href="#/about"
              onClick={() => setOpen(false)}
            >
              Read the notices and the method
            </a>
          </p>
        </div>
      ) : null}
    </div>
  );
}
