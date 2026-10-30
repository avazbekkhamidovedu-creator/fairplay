import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as bridge from "../lib/bridge";
import { getHomeworkToken } from "../lib/homework";
import { clearHomeworkSession, setHomeworkSession, takeSignedOutNotice } from "../lib/homeworkSession";
import HomeworkPage from "../pages/student/HomeworkPage";
import StudentLogin from "../pages/student/StudentLogin";

type Reply = { status: number; body?: unknown };

const START = {
  state: "S".repeat(43),
  start_url: "https://oj.example/x",
  allowed_hosts: ["oj.example"],
  finish_url: "https://oj.example/done",
  resume_paths: ["/"],
  expires_at: new Date(Date.now() + 600_000).toISOString(),
};
const TIMES = { expires_at: new Date(Date.now() + 1_800_000).toISOString(), absolute_expires_at: new Date(Date.now() + 14_400_000).toISOString() };

let replies: Record<string, Reply>;
let calls: { url: string; init?: RequestInit }[];
let posted: { action: string; [k: string]: unknown }[];
let unlockAnswer: Record<string, unknown> | null;

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

function native(remember: { available: boolean; name: string | null } | undefined) {
  const postMessage = vi.fn((msg: { action: string }) => {
    posted.push(msg);
    if (msg.action === "rememberUnlock" && unlockAnswer) setTimeout(() => send({ type: "rememberResult", ...unlockAnswer }), 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  bridge.onNative(() => undefined);
  send({ type: "state", locked: false, trusted: true, version: "1", homeworkLogin: true, ...(remember ? { homeworkRemember: remember } : {}) });
}

function renderApp(path = "/student/login") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/student/login" element={<StudentLogin />} />
        <Route path="/student/homework" element={<HomeworkPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const sent = (a: string) => posted.filter((m) => m.action === a);
const called = (u: string) => calls.filter((c) => c.url === u);

beforeEach(() => {
  calls = [];
  posted = [];
  unlockAnswer = null;
  replies = {
    "/api/homework/login/start": { status: 200, body: START },
    "/api/homework/login/finish": { status: 200, body: { token: "HW1", name: "Ava K", netid: "ak1", ...TIMES } },
    "/api/homework/remember/create": { status: 200, body: { remember_token: "REM1", expires_at: "x" } },
    "/api/homework/remember/exchange": { status: 200, body: { token: "HW2", ...TIMES, remember_token: "REM2", remember_expires_at: "x", name: "Ava K", netid: "ak1" } },
    "/api/homework/remember/forget-all": { status: 200, body: { ok: true } },
    "/api/homework/me": { status: 200, body: { name: "Ava K", netid: "ak1" } },
  };
  localStorage.clear();
  bridge._resetBridgeForTests();
  clearHomeworkSession();
  takeSignedOutNotice();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/health") return new Response(JSON.stringify({ homework_login: { provider: "dkuoj", label: "DKU OJ" } }), { status: 200 });
      calls.push({ url, init });
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

describe("code screen has no remember option", () => {
  it.each([
    ["unavailable", { available: false, name: null }],
    ["missing", undefined],
    ["available", { available: true, name: null }],
    ["remembered", { available: true, name: "Ava K" }],
  ])("shows no checkbox when %s", async (_n, state) => {
    native(state);
    renderApp();
    await screen.findByRole("button", { name: "Log in with DKU OJ" });
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/Remember me/)).toBeNull();
    expect(screen.queryByText(/fingerprint/)).toBeNull();
  });

  it("login creates and saves nothing", async () => {
    native({ available: true, name: null });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Log in with DKU OJ" }));
    await waitFor(() => expect(sent("openHomeworkLogin")).toHaveLength(1));
    send({ type: "homeworkLoginResult", state: START.state, ticket: "TICKET" });
    await screen.findByText("Welcome, Ava K");
    expect(called("/api/homework/remember/create")).toHaveLength(0);
    expect(sent("rememberSave")).toHaveLength(0);
  });
});

describe("Remember this device card", () => {
  async function open(remember: { available: boolean; name: string | null } | undefined) {
    native(remember);
    setHomeworkSession("HW1", TIMES);
    renderApp("/student/homework");
    await screen.findByText("Welcome, Ava K");
  }
  const card = () => screen.queryByRole("button", { name: "Remember this device" });

  it.each([
    ["unavailable", { available: false, name: null }],
    ["missing", undefined],
  ])("is hidden when %s", async (_n, state) => {
    await open(state);
    expect(card()).toBeNull();
    expect(screen.queryByText(/fingerprint/)).toBeNull();
  });

  it("is hidden when this account is already remembered", async () => {
    await open({ available: true, name: "Ava K" });
    expect(card()).toBeNull();
    expect(screen.getByRole("button", { name: "Forget this device" })).toBeTruthy();
  });

  it("is shown for a device remembered under another name", async () => {
    await open({ available: true, name: "Someone Else" });
    expect(card()).toBeTruthy();
  });

  it("success: creates with the Bearer, saves, confirms", async () => {
    await open({ available: true, name: null });
    expect(screen.getByText("Next time you can sign in with your fingerprint or device password.")).toBeTruthy();
    await userEvent.setup().click(card()!);
    await screen.findByText("This device is remembered.");
    const c = called("/api/homework/remember/create");
    expect(c).toHaveLength(1);
    expect((c[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer HW1");
    expect(sent("rememberSave")).toEqual([{ action: "rememberSave", token: "REM1", name: "Ava K" }]);
    expect(card()).toBeNull();
    expect(screen.getByRole("button", { name: "Forget this device" })).toBeTruthy();
  });

  it("failure: plain error, no save, no token shown", async () => {
    replies["/api/homework/remember/create"] = { status: 503, body: {} };
    await open({ available: true, name: null });
    await userEvent.setup().click(card()!);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("DKU OJ cannot be reached right now. Try again in a few minutes.");
    expect(sent("rememberSave")).toHaveLength(0);
    expect(document.body.textContent).not.toMatch(/HW1|REM1/);
    expect(card()).toBeTruthy();
  });

  it("hides once the state refresh reports this account", async () => {
    await open({ available: true, name: null });
    send({ type: "state", locked: false, trusted: true, version: "1", homeworkLogin: true, homeworkRemember: { available: true, name: "Ava K" } });
    await waitFor(() => expect(card()).toBeNull());
  });
});

describe("Continue as", () => {
  async function press() {
    native({ available: true, name: "Ava K" });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Continue as Ava K" }));
    return user;
  }

  it("shows above the login link", async () => {
    native({ available: true, name: "Ava K" });
    renderApp();
    const cont = await screen.findByRole("button", { name: "Continue as Ava K" });
    const login = await screen.findByRole("button", { name: "Log in with DKU OJ" });
    expect(cont.compareDocumentPosition(login) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("success: exchanges, stores the rotated token and opens homework", async () => {
    unlockAnswer = { token: "REM1" };
    await press();
    await screen.findByText("Welcome, Ava K");
    expect(sent("rememberUnlock")).toHaveLength(1);
    expect(JSON.parse(String(called("/api/homework/remember/exchange")[0].init?.body))).toEqual({ remember_token: "REM1" });
    expect(sent("rememberSave")).toEqual([{ action: "rememberSave", token: "REM2", name: "Ava K" }]);
    expect(getHomeworkToken()).toBe("HW2");
  });

  it("401: forgets the stored item and asks to sign in again", async () => {
    unlockAnswer = { token: "REM1" };
    replies["/api/homework/remember/exchange"] = { status: 401, body: {} };
    await press();
    expect((await screen.findByRole("alert")).textContent).toBe("Please sign in again.");
    expect(sent("rememberForget")).toHaveLength(1);
    expect(getHomeworkToken()).toBeNull();
    expect(screen.queryByRole("button", { name: /Continue as/ })).toBeNull();
  });

  it("cancel: nothing happens", async () => {
    unlockAnswer = { cancelled: true };
    await press();
    await waitFor(() => expect((screen.getByRole("button", { name: "Continue as Ava K" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(called("/api/homework/remember/exchange")).toHaveLength(0);
    expect(sent("rememberForget")).toHaveLength(0);
  });

  it("403: shows the no-class message", async () => {
    unlockAnswer = { token: "REM1" };
    replies["/api/homework/remember/exchange"] = { status: 403, body: {} };
    await press();
    expect((await screen.findByRole("alert")).textContent).toBe("Your DKU OJ account is not linked to a class. Ask your professor.");
    expect(sent("rememberForget")).toHaveLength(0);
  });

  it("Use a different account forgets the stored item and shows the normal login", async () => {
    native({ available: true, name: "Ava K" });
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Use a different account" }));
    expect(sent("rememberForget")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Continue as/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Log in with DKU OJ" })).toBeTruthy();
  });
});

describe("sign out and forget", () => {
  it("Sign out clears the session and does not touch the stored item", async () => {
    native({ available: true, name: "Ava K" });
    setHomeworkSession("HW1", TIMES);
    const user = userEvent.setup();
    renderApp("/student/homework");
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(getHomeworkToken()).toBeNull();
    expect(sent("rememberForget")).toHaveLength(0);
    expect(called("/api/homework/remember/forget-all")).toHaveLength(0);
  });

  it("Forget this device calls forget-all with the Bearer, deletes the stored item and signs out", async () => {
    native({ available: true, name: "Ava K" });
    setHomeworkSession("HW1", TIMES);
    const user = userEvent.setup();
    renderApp("/student/homework");
    await user.click(await screen.findByRole("button", { name: "Forget this device" }));
    await screen.findByText("Enter your exam code");
    const c = called("/api/homework/remember/forget-all");
    expect(c).toHaveLength(1);
    expect((c[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer HW1");
    expect(sent("rememberForget")).toHaveLength(1);
    expect(getHomeworkToken()).toBeNull();
  });
});

describe("exam code entry", () => {
  it("drops the homework token and sends nothing to the app about it", async () => {
    native({ available: true, name: null });
    setHomeworkSession("HW1", TIMES);
    replies["/api/student/code-login/challenge"] = { status: 200, body: { nonce: "NONCE-12345678", expires_in: 120 } };
    replies["/api/student/code-login"] = { status: 400, body: { detail: "That code is not valid." } };
    const user = userEvent.setup();
    renderApp();
    await screen.findByText("Enter your exam code");
    await user.keyboard("ABC123");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(getHomeworkToken()).toBeNull();
    expect(posted.map((m) => m.action).filter((a) => a !== "attest")).toEqual([]);
  });
});
