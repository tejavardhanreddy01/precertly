import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

// jsdom has neither of these; the app guards for their absence, and tests replace them when
// they need to observe a call.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
// jsdom prints "not implemented" for window.scrollTo; the app only calls it on route changes.
window.scrollTo = (() => {}) as typeof window.scrollTo;
// Tests run at a desktop width (three panes side by side); a test that needs the tabbed layout
// replaces window.matchMedia with `narrowScreen()` from ./render.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: query.includes("min-width: 1024px"),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}
