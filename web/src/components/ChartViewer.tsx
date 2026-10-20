import { useEffect, useMemo, useRef, useState } from "react";
import { highlight, type Span } from "../lib/highlight";
import type { ChartChunk } from "../lib/types";

/** What the viewer should scroll to: a chunk, and optionally one citation's first mark in it. */
export interface Target {
  chunkId?: string;
  citeKey?: number;
}

interface Group {
  key: string;
  title: string;
  date: string | null;
  isRequest: boolean;
  chunks: { chunk: ChartChunk; index: number }[];
}

function groupChunks(chart: readonly ChartChunk[]): Group[] {
  const groups = new Map<string, Group>();
  chart.forEach((chunk, index) => {
    const isRequest = chunk.source === "request";
    const key = isRequest ? "request" : `${chunk.resource_type}/${chunk.resource_id}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        title: isRequest ? "Request fields" : key,
        date: isRequest ? null : chunk.effective_date,
        isRequest,
        chunks: [],
      };
      groups.set(key, group);
    }
    group.chunks.push({ chunk, index });
  });
  // The request fields go last so the chart reads first.
  return [...groups.values()].sort((a, b) => Number(a.isRequest) - Number(b.isRequest));
}

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollWithin(container: HTMLElement, element: HTMLElement): void {
  const top =
    element.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 12;
  if (typeof container.scrollTo === "function") {
    container.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? "auto" : "smooth" });
  } else {
    container.scrollTop = Math.max(0, top);
  }
}

function Highlighted({ text, spans, activeKey }: { text: string; spans: readonly Span[]; activeKey?: number }) {
  const segments = useMemo(() => highlight(text, spans), [text, spans]);
  return (
    <>
      {segments.map((s, i) =>
        s.marked ? (
          <mark key={i} data-cites={s.keys.join(" ")} data-active={activeKey !== undefined && s.keys.includes(activeKey)}>
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false" className="shrink-0">
      <path d={open ? "M2 4l4 4 4-4" : "M4 2l4 4-4 4"} fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

function ChunkRow({
  chunk,
  index,
  open,
  onToggle,
  spans,
  activeKey,
}: {
  chunk: ChartChunk;
  index: number;
  open: boolean;
  onToggle: () => void;
  spans: readonly Span[] | undefined;
  activeKey: number | undefined;
}) {
  const cited = !!spans && spans.length > 0;
  const bodyId = `chunk-body-${index}`;
  const name = chunk.source === "request" ? chunk.chunk_id : `chunk ${chunk.chunk_index}`;
  const preview = chunk.text.replace(/\s+/g, " ").slice(0, 90);
  return (
    <div id={`chunk-${index}`} data-chunk-id={chunk.chunk_id} data-cited={cited} className="border-t border-slate-200 first:border-t-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm hover:bg-slate-100"
      >
        <Chevron open={open} />
        <span className="shrink-0 font-medium">{name}</span>
        {cited ? (
          <span className="shrink-0 rounded border border-slate-900 bg-yellow-200 px-1 text-xs font-semibold text-slate-900">
            highlighted
          </span>
        ) : null}
        {!open ? <span className="truncate text-slate-700">{preview}</span> : null}
      </button>
      {open ? (
        <pre id={bodyId} className="whitespace-pre-wrap break-words px-2 pb-2 pl-7 font-sans text-sm leading-relaxed">
          <Highlighted text={chunk.text} spans={spans ?? []} activeKey={activeKey} />
        </pre>
      ) : null}
    </div>
  );
}

/**
 * The chart as chunks, grouped by resource, with request fields in a group of their own.
 * Chunks that carry highlights are open. Mount it with a new `key` when the selection changes:
 * that resets what the reader opened by hand.
 */
export function ChartViewer({
  chart,
  highlights,
  target,
}: {
  chart: readonly ChartChunk[];
  highlights: ReadonlyMap<string, readonly Span[]>;
  target: Target;
}) {
  const groups = useMemo(() => groupChunks(chart), [chart]);
  const [toggled, setToggled] = useState<ReadonlyMap<string, boolean>>(new Map());
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = container.current;
    if (!root || target.chunkId === undefined) return;
    const row = [...root.querySelectorAll<HTMLElement>("[data-chunk-id]")].find(
      (el) => el.dataset.chunkId === target.chunkId,
    );
    if (!row) return;
    const mark =
      target.citeKey === undefined
        ? null
        : [...row.querySelectorAll<HTMLElement>("mark[data-cites]")].find((m) =>
            (m.dataset.cites ?? "").split(" ").includes(String(target.citeKey)),
          );
    scrollWithin(root, mark ?? row);
  }, [target.chunkId, target.citeKey]);

  return (
    <div
      ref={container}
      className="max-h-[70vh] overflow-y-auto rounded border border-slate-300 bg-white"
      data-testid="chart-viewer"
      tabIndex={0}
      role="region"
      aria-label="Chart text, grouped by resource"
    >
      {groups.map((group) => (
        <section key={group.key} aria-label={group.title} className="border-b border-slate-300 last:border-b-0">
          <h3 className="sticky top-0 z-10 flex flex-wrap items-baseline gap-x-2 border-b border-slate-200 bg-slate-100 px-2 py-1 text-sm font-semibold">
            <span className="break-all">{group.title}</span>
            {group.date ? <span className="font-normal text-slate-700">{group.date}</span> : null}
          </h3>
          {group.chunks.map(({ chunk, index }) => {
            const spans = highlights.get(chunk.chunk_id);
            const cited = !!spans && spans.length > 0;
            const open = toggled.get(chunk.chunk_id) ?? cited;
            return (
              <ChunkRow
                key={chunk.chunk_id}
                chunk={chunk}
                index={index}
                open={open}
                spans={spans}
                activeKey={chunk.chunk_id === target.chunkId ? target.citeKey : undefined}
                onToggle={() => setToggled((prev) => new Map(prev).set(chunk.chunk_id, !open))}
              />
            );
          })}
        </section>
      ))}
    </div>
  );
}
