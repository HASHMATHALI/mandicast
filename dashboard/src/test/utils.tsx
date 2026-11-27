// Test helpers: a fetch stand-in that answers by URL path, and a renderer with the app's providers.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";

export interface Call {
  method: string;
  url: URL;
  headers: Record<string, string>;
  body: unknown;
}

type Reply = { status?: number; body?: unknown; headers?: Record<string, string> };
type Handler = (call: Call) => Reply;

/** A route answer with a status other than 200: `reply(422, {...})`. */
export const reply = (status: number, body?: unknown, headers?: Record<string, string>): Handler =>
  () => ({ status, body, headers });

/** Replaces global fetch. `routes` maps "METHOD /v1/path" (query ignored) to a handler (see `reply`) or a JSON body
 * returned with status 200. */
export function mockFetch(routes: Record<string, Handler | object>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>)
      .map(([k, v]) => [k.toLowerCase(), v]));
    const call: Call = { method, url, headers, body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const route = routes[`${method} ${url.pathname}`];
    if (!route) return new Response(JSON.stringify({ error: { code: "not_found", message: "no mock" } }), { status: 404 });
    const out = typeof route === "function" ? (route as Handler)(call) : { body: route };
    const status = out.status ?? 200;
    return new Response(status === 204 ? null : JSON.stringify(out.body ?? {}),
                        { status, headers: { "Content-Type": "application/json", ...(out.headers ?? {}) } });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export function renderAt(path: string, routePattern: string, element: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePattern} element={element} />
          <Route path="*" element={<p>other page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
