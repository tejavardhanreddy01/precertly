import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

async function start(target: HTMLElement) {
  // __LIVE_ENABLED__ is a build-time constant (vite.config.ts). With live mode off it is the
  // literal false, this import is unreachable, and the build contains no live code.
  const live = __LIVE_ENABLED__ ? (await import("./live/boot")).liveFeature : undefined;
  createRoot(target).render(
    <StrictMode>
      <App live={live} />
    </StrictMode>,
  );
}

void start(root);
