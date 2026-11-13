import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as bridge from "../lib/bridge";
import { getHomeworkToken } from "../lib/homework";
import { clearHomeworkSession, setHomeworkSession } from "../lib/homeworkSession";
import HomeworkPage from "../pages/student/HomeworkPage";
import StudentLogin from "../pages/student/StudentLogin";

type Reply = { status: number; body?: unknown };

const START = {
  state: "S".repeat(43),
  start_url: "https://oj.example/fairplay/app-login/?state=x&challenge=y",
  allowed_hosts: ["oj.example", "idp.example"],
  finish_url: "https://oj.example/fairplay/app-login/done",
  resume_paths: ["/"],
  expires_at: new Date(Date.now() + 600_000).toISOString(),
};
const FINISH = { token: "HW-TOKEN", name: "Ava K", netid: "ak1", expires_at: "x" };
const HEALTH_ON = { ok: true, homework_login: { provider: "dkuoj", label: "DKU OJ" } };
const EXAM_KEY = "proctora:token:student";

let health: unknown;
let replies: Record<string, Reply>;
let calls: { url: string; init?: RequestInit }[];
let posted: { action: string; [k: string]: unknown }[];

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

function native(supports: boolean | undefined) {
  const postMessage = vi.fn((msg: { action: string }) => {
    posted.push(msg);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  bridge.onNative(() => undefined);
  send({ type: "state", locked: false, trusted: true, version: "1", ...(supports === undefined ? {} : { homeworkLogin: supports }) });
}

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/student/login"]}>
      <Routes>
        <Route path="/student/login" element={<StudentLogin />} />
        <Route path="/student/homework" element={<HomeworkPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const link = () => screen.queryByRole("button", { name: /Log in with/ });

beforeEach(() => {
  calls = [];
  posted = [];
  health = HEALTH_ON;
  replies = {
    "/api/homework/login/start": { status: 200, body: START },
    "/api/homework/login/finish": { status: 200, body: FINISH },
    "/api/homework/me": { status: 200, body: { name: "Ava K", netid: "ak1" } },
  };
  localStorage.clear();
  clearHomeworkSession();
  bridge._resetBridgeForTests();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/health") return new Response(JSON.stringify(health), { status: 200 });
      const r = replies[url];
      if (!r) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify(r.body ?? {}), { status: r.status });
    }),
  );
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  vi.unstubAllGlobals();
});

async function begin() {
  const user = userEvent.setup();
  renderLogin();
  await user.click(await screen.findByRole("button", { name: "Log in with DKU OJ" }));
  await waitFor(() => expect(posted.some((m) => m.action === "openHomeworkLogin")).toBe(true));
  return user;
}

describe("homework login link", () => {
  it("is hidden when the server has it off", async () => {
    health = { ok: true, homework_login: null };
    native(true);
    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() => expect(calls.some((c) => c.url === "/api/health")).toBe(true));
    expect(link()).toBeNull();
  });

  it("is hidden when the field is missing", async () => {
    health = { ok: true };
    native(true);
    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(link()).toBeNull();
  });

  it("is hidden without a native bridge", async () => {
    renderLogin();
    await waitFor(() => expect(calls.some((c) => c.url === "/api/health")).toBe(true));
    expect(link()).toBeNull();
    expect(screen.queryByText("Homework?")).toBeNull();
  });

  it.each([false, undefined])("is hidden when the app lacks support (%s)", async (flag) => {
    native(flag);
    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() => expect(calls.some((c) => c.url === "/api/health")).toBe(true));
    expect(link()).toBeNull();
  });

  it("shows under the code form and keeps Test my device", async () => {
    native(true);
    renderLogin();
    expect(await screen.findByRole("button", { name: "Log in with DKU OJ" })).toBeTruthy();
    expect(screen.getByText("Homework?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test my device" })).toBeTruthy();
  });
});

describe("homework login flow", () => {
  it("sends the exact bridge payload", async () => {
    native(true);
    await begin();
    expect(posted.find((m) => m.action === "openHomeworkLogin")).toEqual({
      action: "openHomeworkLogin",
      state: START.state,
      start_url: START.start_url,
      allowed_hosts: START.allowed_hosts,
      finish_url: START.finish_url,
      resume_paths: START.resume_paths,
    });
  });

  it("ignores a result for another state, then finishes on the right one", async () => {
    native(true);
    localStorage.setItem(EXAM_KEY, "EXAM-TOK");
    await begin();
    send({ type: "homeworkLoginResult", state: "OTHER", ticket: "BAD" });
    await new Promise((r) => setTimeout(r, 20));
    expect(calls.some((c) => c.url === "/api/homework/login/finish")).toBe(false);
    send({ type: "homeworkLoginResult", state: START.state, ticket: "TICKET" });
    expect(await screen.findByText("Welcome, Ava K")).toBeTruthy();
    const fin = calls.find((c) => c.url === "/api/homework/login/finish");
    expect(JSON.parse(String(fin?.init?.body))).toEqual({ state: START.state, ticket: "TICKET" });
    expect(getHomeworkToken()).toBe("HW-TOKEN");
    expect(localStorage.getItem(EXAM_KEY)).toBe("EXAM-TOK");
    expect(Object.keys(localStorage).filter((k) => localStorage.getItem(k) === "HW-TOKEN")).toEqual([]);
  });

  it("cancel returns quietly", async () => {
    native(true);
    await begin();
    send({ type: "homeworkLoginResult", cancelled: true });
    await waitFor(() => expect((screen.getByRole("button", { name: "Log in with DKU OJ" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(getHomeworkToken()).toBeNull();
    expect(calls.some((c) => c.url === "/api/homework/login/finish")).toBe(false);
  });

  it("shows a bridge error", async () => {
    native(true);
    await begin();
    send({ type: "homeworkLoginResult", state: START.state, error: "boom-secret" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("The sign-in window could not be completed. Try again.");
    expect(document.body.textContent).not.toContain("boom-secret");
  });

  it.each([
    [400, "Sign-in expired, try again."],
    [403, "Your DKU OJ account is not linked to a class. Ask your professor."],
    [429, "Too many tries. Wait a moment and try again."],
    [503, "DKU OJ cannot be reached right now. Try again in a few minutes."],
  ])("finish %i shows its message and stores nothing", async (status, text) => {
    native(true);
    replies["/api/homework/login/finish"] = { status, body: { detail: "x" } };
    await begin();
    send({ type: "homeworkLoginResult", state: START.state, ticket: "T-SECRET" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(text);
    expect(document.body.textContent).not.toContain("T-SECRET");
    expect(getHomeworkToken()).toBeNull();
  });

  it("start 429 shows the rate message and opens no window", async () => {
    native(true);
    replies["/api/homework/login/start"] = { status: 429, body: {} };
    const user = userEvent.setup();
    renderLogin();
    await user.click(await screen.findByRole("button", { name: "Log in with DKU OJ" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too many tries. Wait a moment and try again.");
    expect(posted.some((m) => m.action === "openHomeworkLogin")).toBe(false);
  });
});

describe("homework page", () => {
  function renderPage() {
    return render(
      <MemoryRouter initialEntries={["/student/homework"]}>
        <Routes>
          <Route path="/student/homework" element={<HomeworkPage />} />
          <Route path="/student/login" element={<p>Code screen</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("redirects to the code screen without a token and calls nothing", async () => {
    renderPage();
    expect(await screen.findByText("Code screen")).toBeTruthy();
    expect(calls).toHaveLength(0);
  });

  it("loads /me with the homework token and no exam endpoint", async () => {
    setHomeworkSession("HW-TOKEN");
    renderPage();
    expect(await screen.findByText("Welcome, Ava K")).toBeTruthy();
    expect(screen.getByText("NetID: ak1")).toBeTruthy();
    expect(screen.getByText("Homework is coming soon.")).toBeTruthy();
    expect(calls.map((c) => c.url).filter((u) => u !== "/api/homework/webtest")).toEqual(["/api/homework/me"]);
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer HW-TOKEN");
  });

  it("redirects and drops the token when /me says 401", async () => {
    setHomeworkSession("OLD");
    replies["/api/homework/me"] = { status: 401, body: {} };
    renderPage();
    expect(await screen.findByText("Code screen")).toBeTruthy();
    expect(getHomeworkToken()).toBeNull();
  });

  it("sign out drops the token, leaves the exam token and tells a supporting app", async () => {
    native(true);
    setHomeworkSession("HW-TOKEN");
    localStorage.setItem(EXAM_KEY, "EXAM-TOK");
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("Code screen")).toBeTruthy();
    expect(getHomeworkToken()).toBeNull();
    expect(localStorage.getItem(EXAM_KEY)).toBe("EXAM-TOK");
    expect(posted.filter((m) => m.action === "clearHomeworkLogin")).toHaveLength(1);
  });

  it("sign out sends nothing to an app without support", async () => {
    native(false);
    setHomeworkSession("HW-TOKEN");
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    await screen.findByText("Code screen");
    expect(posted).toHaveLength(0);
  });
});
