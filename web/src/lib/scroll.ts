/** Space kept above a mark that is taller than the visible area, in pixels. */
export const MARK_MARGIN = 24;

export interface ScrollInput {
  /** Height of the scroll container's visible area. */
  containerHeight: number;
  /** Height of everything inside the scroll container (its scrollHeight). */
  contentHeight: number;
  /** Current scrollTop of the container. */
  scrollTop: number;
  /** Top and height of the mark, measured from the top of the scroll content (not the viewport). */
  mark: { top: number; height: number } | null;
  /** Height of anything pinned over the top of the visible area, such as a sticky heading. */
  topInset?: number;
}

/**
 * Where to set scrollTop so the first highlighted mark is centered in the visible part of the
 * container (the container less the pinned area). A mark that fits there, even with no room to spare,
 * is centered so all of it is visible. Only a mark taller than the visible area is aligned with its
 * top MARK_MARGIN under the pinned area instead. The result is rounded and clamped to the
 * scrollable range; with no mark the current position is kept.
 */
export function targetScrollTop({ containerHeight, contentHeight, scrollTop, mark, topInset = 0 }: ScrollInput): number {
  const max = Math.max(0, contentHeight - containerHeight);
  const clamp = (value: number) => Math.min(max, Math.max(0, Math.round(value)));
  if (!mark) return clamp(scrollTop);
  const visible = Math.max(0, containerHeight - topInset);
  if (mark.height <= visible) {
    // The mark's center goes to the center of the visible area.
    return clamp(mark.top + mark.height / 2 - (topInset + visible / 2));
  }
  return clamp(mark.top - topInset - MARK_MARGIN);
}
