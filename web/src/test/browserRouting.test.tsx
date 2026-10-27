import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import * as bridge from "../lib/bridge";

const loaded = vi.hoisted(() => ({ routes: 0, api: 0 }));

vi.mock("../pages/student/routes", () => {
  loaded.routes += 1;
  return { default: () => <p>student screens</p> };
});
vi.mock("../api/student", () => {
  loaded.api += 1;
  return {};
});

function native() {
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: vi.fn() } } };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  loaded.routes = 0;
  loaded.api = 0;
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  vi.unstubAllGlobals();
});

describe("student routes in a plain browser", () => {
  it.each(["/student/login", "/student", "/student/exam/a_1", "/student/exams/e_1/start"])(
    "%s renders nothing and loads no student code",
    async (path) => {
      const { container } = renderAt(path);
      await new Promise((r) => setTimeout(r, 20));
      expect(container.innerHTML).toBe("");
      expect(loaded).toEqual({ routes: 0, api: 0 });
    },
  );

  it("does not redirect to a login screen", async () => {
    const { container } = renderAt("/student/login");
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/sign in/i)).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("the teacher login still renders and never touches the student chunk", async () => {
    renderAt("/teacher/login");
    expect(await screen.findByRole("heading", { name: "Teacher sign in" })).toBeInTheDocument();
    expect(loaded).toEqual({ routes: 0, api: 0 });
  });

  it("the root still goes to the teacher side", async () => {
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Teacher sign in" })).toBeInTheDocument();
    expect(loaded.routes).toBe(0);
  });
});

describe("student routes inside the app", () => {
  it("render once the bridge is there", async () => {
    native();
    renderAt("/student/login");
    expect(await screen.findByText("student screens")).toBeInTheDocument();
    expect(loaded.routes).toBe(1);
  });
});
