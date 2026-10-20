import { render, screen } from "@testing-library/react";
import { App } from "../App";
import { DataStore } from "../lib/data";
import { diskFetch } from "./fixtures";

/** Renders the whole app at a hash route against the recorded files on disk. */
export function renderAt(hash: string, store = new DataStore(diskFetch())) {
  window.location.hash = hash;
  return render(<App store={store} />);
}

/** Waits for the notices strip, which appears once index.json has loaded. */
export async function loaded() {
  return screen.findByRole("region", { name: "Notices" });
}
