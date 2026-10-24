import { useSyncExternalStore } from "react";
import { IconAlert, IconInfo } from "../components/icons";
import { useIndex } from "../lib/index-context";
import type { ApiClient } from "../lib/live/http";
import { MESSAGES } from "../lib/live/messages";
import type { RunView } from "../lib/live/poller";
import type { LiveSession } from "../lib/live/session";
import { href, pushHash } from "../lib/router";
import { useTitle } from "../lib/title";
import { useProbe, useRun } from "./hooks";
import { ResultView } from "./ResultView";
import { RunProgress } from "./RunProgress";

const button =
  "inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50";
const primary = `${button} bg-accent text-on-accent hover:bg-accent-strong`;
const secondary = `${button} border border-line-strong bg-surface text-ink hover:bg-sunken`;
const textLink = "font-medium text-accent underline underline-offset-2";

function currentHash(): string {
  return window.location.hash || "#/live";
}

function EnvironmentBanner({ state, onCheck }: { state: "checking" | "up" | "down"; onCheck: () => void }) {
  return (
    <section aria-label="Live environment" className="rounded-lg border border-line bg-surface p-4" data-testid="environment">
      <p role="status" aria-live="polite" className="flex items-start gap-2" data-state={state}>
        <IconInfo size={16} className="mt-1 shrink-0 text-ink-3" />
        <span>
          {state === "checking"
            ? "Checking whether the live environment is running…"
            : state === "up"
              ? "The live environment is running."
              : MESSAGES.down}
        </span>
      </p>
      {state === "down" ? (
        <p className="mt-2 text-sm text-ink-2">
          It is switched on only when someone starts it, so this is the usual state.{" "}
          <a className={textLink} href="#/cases">
            Browse the recorded cases
          </a>
          .
        </p>
      ) : null}
      <p className="mt-3">
        <button type="button" className={secondary} onClick={onCheck} disabled={state === "checking"}>
          Check again
        </button>
      </p>
    </section>
  );
}

function SignIn({
  session,
  expiredRun,
}: {
  session: LiveSession;
  /** True when the run ended because the sign-in is no longer accepted. */
  expiredRun: boolean;
}) {
  const snap = useSyncExternalStore(session.subscribe, session.getSnapshot);
  if (snap.signedIn && !expiredRun) {
    return (
      <section aria-label="Sign-in" className="rounded-lg border border-line bg-surface p-4" data-testid="signin">
        <p>You are signed in.</p>
        <p className="mt-3">
          <button type="button" className={secondary} onClick={() => session.signOut()}>
            Sign out
          </button>
        </p>
      </section>
    );
  }
  const again = snap.expired || expiredRun;
  return (
    <section aria-label="Sign-in" className="rounded-lg border border-line bg-surface p-4" data-testid="signin">
      <p role="status" data-testid="signin-notice">
        {again
          ? MESSAGES.expired
          : (snap.notice ??
            "Only invited users can run a case. Signing in takes a password and a code from an authenticator app.")}
      </p>
      <p className="mt-3">
        <button
          type="button"
          className={primary}
          disabled={snap.busy}
          onClick={() => void session.beginSignIn(currentHash())}
        >
          {again ? "Sign in again" : "Sign in"}
        </button>
      </p>
    </section>
  );
}

function RunEnd({ view, onDismiss }: { view: Extract<RunView, { phase: "error" | "ended" }>; onDismiss: () => void }) {
  const title =
    view.phase === "ended" ? (view.state.status === "rejected" ? "The run was not started" : "The run failed") : "The run stopped";
  return (
    <section
      role="alert"
      aria-labelledby="end-h"
      className="rounded-lg border border-danger-deep bg-surface p-4"
      data-testid="run-end"
    >
      <h2 id="end-h" className="flex items-center gap-2 text-lg font-bold text-notmet-fg">
        <IconAlert size={18} className="shrink-0 text-danger" />
        {title}
      </h2>
      <p className="mt-1">{view.message}</p>
      <p className="mt-3">
        <button type="button" className={secondary} onClick={onDismiss}>
          Dismiss
        </button>
      </p>
    </section>
  );
}

/**
 * The live page, at #/live and #/live/CASE_ID. It asks the environment once when it opens, offers
 * sign-in, runs one case at a time and follows the run to its result. It is a page that scrolls, not
 * a dialog, because a result is long. Nothing on it runs until the visitor presses a button.
 */
export function LiveScreen({
  caseId,
  session,
  client,
}: {
  caseId: string | undefined;
  session: LiveSession;
  client: ApiClient;
}) {
  useTitle("Live run");
  const { cases } = useIndex();
  const snap = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const { state, check } = useProbe(client);
  const { view, controller } = useRun(client, session);

  const known = caseId !== undefined && cases.some((c) => c.id === caseId);
  const active = view.phase === "starting" || view.phase === "running";
  const expiredRun = view.phase === "error" && view.kind === "expired";
  const blocker = !snap.signedIn
    ? "Sign in first."
    : state !== "up"
      ? state === "checking"
        ? "Waiting for the environment check."
        : "The live environment is not running."
      : !known
        ? "Choose a case."
        : null;
  const canRun = blocker === null && !active;

  return (
    <div className="mx-auto max-w-text space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Live run</h1>
        <p className="mt-2 text-ink-2">
          Run the judgment on one synthetic case now, instead of reading a recorded run. The page is a separate part of
          the site: the recorded runs elsewhere do not change, and a person still decides, because nothing here approves
          or denies a request.
        </p>
      </div>

      <EnvironmentBanner state={state} onCheck={check} />
      <SignIn session={session} expiredRun={expiredRun} />

      <section aria-label="Choose a case" className="rounded-lg border border-line bg-surface p-4">
        <label htmlFor="live-case" className="block font-semibold">
          Case
        </label>
        <select
          id="live-case"
          className="mt-1 block w-full rounded-md border border-line-strong bg-surface px-2 py-2 font-mono text-sm"
          value={known ? caseId : ""}
          disabled={active}
          onChange={(e) => pushHash(e.target.value === "" ? href(["live"]) : href(["live", e.target.value]))}
        >
          <option value="">Choose a case…</option>
          {cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id} · {c.variant}
            </option>
          ))}
        </select>
        {caseId !== undefined && !known ? (
          <p className="mt-2 text-sm text-notmet-fg" role="status">
            {MESSAGES.notAvailableCase}
          </p>
        ) : null}
        <p className="mt-3 text-sm text-ink-3">
          Starting a run sends the case id to the live environment. The chart is the synthetic chart already in that
          environment; nothing you type is sent.
        </p>
        <p className="mt-3">
          <button
            type="button"
            className={primary}
            disabled={!canRun}
            aria-describedby={blocker ? "run-blocker" : undefined}
            onClick={() => {
              if (known && caseId !== undefined) void controller.start(caseId);
            }}
          >
            Run live
          </button>
        </p>
        {blocker ? (
          <p id="run-blocker" className="mt-2 text-sm text-ink-3">
            {blocker}
          </p>
        ) : null}
      </section>

      {view.phase === "starting" || view.phase === "running" ? <RunProgress view={view} /> : null}
      {view.phase === "error" || view.phase === "ended" ? (
        <RunEnd view={view} onDismiss={() => controller.reset()} />
      ) : null}
      {view.phase === "done" && view.state.result ? (
        <ResultView result={view.state.result} timings={view.state.timings} />
      ) : null}
    </div>
  );
}
