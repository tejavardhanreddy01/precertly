import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import { LIVE_VARIABLES, readLiveConfig } from "./scripts/live-config.mjs";

// base './' makes every asset and data URL relative, so the build works from a bucket root
// (or any folder) with no rewrite rules; routes are hash routes for the same reason.
//
// Live mode is a build-time switch. With none of the three VITE_LIVE_* variables set (a plain
// build, CI, the tests) __LIVE_ENABLED__ is the literal false: main.tsx never imports the live
// module, so no live code and no live host reaches dist. Half-set or malformed values fail the
// build here, before anything is written.
export default defineConfig(({ mode }) => {
  const fileEnv = loadEnv(mode, process.cwd(), "VITE_LIVE_");
  const env: Record<string, string | undefined> = {};
  for (const name of LIVE_VARIABLES) env[name] = process.env[name] ?? fileEnv[name];
  // The tests always run recorded-only, whatever the shell has set; they pass the live pieces in.
  const live = process.env.VITEST ? ({ enabled: false } as const) : readLiveConfig(env);
  return {
    base: "./",
    plugins: [react(), tailwindcss()],
    // One script file even in a live build: the live module is imported dynamically (main.tsx),
    // and keeping it in the same file means the first .js asset is the one that names the live
    // hosts, which `deploy.sh web smoke` checks. A recorded-only build has no dynamic import.
    build: { sourcemap: false, rollupOptions: { output: { inlineDynamicImports: true } } },
    define: {
      __LIVE_ENABLED__: JSON.stringify(live.enabled),
      __LIVE_API_ORIGIN__: JSON.stringify(live.enabled ? live.apiOrigin : ""),
      __LIVE_HOSTED_UI_ORIGIN__: JSON.stringify(live.enabled ? live.hostedUiOrigin : ""),
      __LIVE_CLIENT_ID__: JSON.stringify(live.enabled ? live.clientId : ""),
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});
