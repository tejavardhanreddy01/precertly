import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// base './' makes every asset and data URL relative, so the build works from a bucket root
// (or any folder) with no rewrite rules; routes are hash routes for the same reason.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: { sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
