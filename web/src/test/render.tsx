import { render, screen } from "@testing-library/react";
import { App } from "../App";
import { DataStore } from "../lib/data";
import { diskFetch } from "./fixtures";

/** Renders the whole app at a hash route against the recorded files on disk. */
export function renderAt(hash: string, store = new DataStore(diskFetch())) {
  window.location.hash = hash;
  return render(<App store={store} />);
}

/** Waits for the run chip in the top bar, which appears once index.json has loaded. */
export async function loaded() {
  return screen.findByRole("button", { name: /Recorded runs on synthetic data/ });
}

/** Makes the page a narrow screen (under 1024 px), where a case shows tabs instead of three panes. */
export function narrowScreen() {
  window.matchMedia = ((query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList) as typeof window.matchMedia;
}
