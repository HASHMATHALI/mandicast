/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

/** Production builds get a strict Content-Security-Policy (docs/14 §4). The dev server is left without it because
 * React Fast Refresh injects an inline script. */
function contentSecurityPolicy(apiOrigin: string): Plugin {
  const policy = [
    "default-src 'self'",
    `connect-src 'self'${apiOrigin ? ` ${apiOrigin}` : ""}`,
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
  return {
    name: "mandicast-csp",
    apply: "build",
    transformIndexHtml: () => [
      { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: policy }, injectTo: "head-prepend" },
    ],
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const base = env.VITE_API_BASE || "http://127.0.0.1:8000";
  // "same-origin": the API is proxied under /v1 by the dashboard's own web server, so 'self' is enough.
  const apiOrigin = base === "same-origin" ? "" : new URL(base).origin;
  return {
    plugins: [react(), contentSecurityPolicy(apiOrigin)],
    server: { host: "localhost", port: 5173, strictPort: true },
    preview: { host: "localhost", port: 5173, strictPort: true },
    build: { target: "es2022", sourcemap: false },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});
