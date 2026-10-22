/** True when the visitor asked the system for less motion. jsdom has no matchMedia. */
export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Brings an element into view in the middle of the window, smoothly unless the visitor prefers
 * reduced motion. `scrollIntoView` is called only if it exists (jsdom lacks it).
 */
export function reveal(element: Element | null | undefined): void {
  element?.scrollIntoView?.({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
}
