import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { browserTimers, probe } from "../lib/live/api";
import type { ApiClient } from "../lib/live/http";
import { RunController, type RunView } from "../lib/live/poller";
import type { LiveSession } from "../lib/live/session";

export type ProbeState = "checking" | "up" | "down";

/**
 * Asks the live environment once, when the live section is opened, and again only when `check` is
 * called. No timer. The component stays mounted when the visitor moves between #/live and
 * #/live/CASE_ID, so that move does not ask again; leaving the section and coming back does.
 */
export function useProbe(client: ApiClient): { state: ProbeState; check: () => void } {
  const [state, setState] = useState<ProbeState>("checking");
  const mounted = useRef(false);
  const started = useRef(false);
  const current = useRef<AbortController | null>(null);

  const check = useCallback(() => {
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    setState("checking");
    void probe(client, { signal: controller.signal }).then((result) => {
      // A newer check replaced this one, or the page is gone.
      if (controller.signal.aborted || !mounted.current) return;
      setState(result);
    });
  }, [client]);

  useEffect(() => {
    mounted.current = true;
    // React StrictMode mounts, unmounts and mounts again in development; the ref keeps that to one request.
    if (!started.current) {
      started.current = true;
      check();
    }
    return () => {
      mounted.current = false;
    };
  }, [check]);

  return { state, check };
}

/** One run controller for the page, disposed (request aborted, timer cleared) when it goes away. */
export function useRun(client: ApiClient, session: LiveSession): { view: RunView; controller: RunController } {
  const [controller] = useState(
    () =>
      new RunController({
        client,
        now: () => Date.now(),
        sessionActive: () => session.isActive(),
        ...browserTimers,
      }),
  );
  useEffect(() => {
    controller.activate();
    return () => controller.dispose();
  }, [controller]);
  const view = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  return { view, controller };
}

/** Whole seconds since `startedAtMs`, updated once a second while mounted. */
export function useElapsedSeconds(startedAtMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return Math.max(0, Math.floor((now - startedAtMs) / 1000));
}
