import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// Without vitest globals, Testing Library cannot register its own cleanup: unmount after every test.
afterEach(() => cleanup());
