import { useRef, type KeyboardEvent } from "react";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  /** The id of the pane this tab shows. */
  controls: string;
}

/**
 * A segmented control that follows the tabs pattern: one tab is in the tab order, the arrow keys
 * move between tabs (wrapping), Home and End jump to the first and last, and the focused tab is
 * selected at once. The tabs share one row in equal columns through utility classes, not an inline
 * style, so the page needs no `style-src 'unsafe-inline'`.
 */
export function SegmentedTabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  const refs = useRef(new Map<T, HTMLButtonElement>());

  const move = (to: number) => {
    const next = tabs[(to + tabs.length) % tabs.length];
    if (!next) return;
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = tabs.findIndex((t) => t.id === value);
    const keys: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: tabs.length - 1,
    };
    const to = keys[e.key];
    if (to === undefined) return;
    e.preventDefault();
    move(to);
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className="grid shrink-0 auto-cols-fr grid-flow-col gap-1 rounded-lg bg-sunken p-1"
    >
      {tabs.map((tab) => {
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              if (el) refs.current.set(tab.id, el);
              else refs.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={tab.controls}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={`rounded-md px-3 py-2 text-sm font-semibold ${
              selected ? "bg-surface text-ink shadow-sm ring-1 ring-line" : "text-ink-2 hover:bg-surface/60"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
