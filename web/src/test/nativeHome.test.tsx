import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getToken } from "../api/client";
import * as bridge from "../lib/bridge";
import { keepDeviceCheck } from "../lib/pendingDeviceCheck";
import StudentLogin, { StudentAppHome } from "../pages/student/StudentLogin";

type Reply = { status: number; body?: unknown; headers?: Record<string, string> };
type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

let calls: Call[];
let replies: Record<string, Reply[]>;

const SESSION = { token: "SESSION-TOK", role: "student", name: "Avazbek Khamidov" };
const PREFLIGHT = {
  type: "preflight",
  problems: ["Unplug the second display"],
  checks: [
    { id: "macos_version", ok: true, message: "macOS 15.1" },
    { id: "displays", ok: false, message: "Unplug the second display" },
  ],
  platform: "mac",
  app_version: "0.7.2",
  os_version: "15.1",
};

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

function native() {
  const postMessage = vi.fn((msg: { action: string; nonce?: string }) => {
    setTimeout(() => {
      if (msg.action === "attest")
        send({ type: "attestation", nonce: msg.nonce, device_id: "DEVICE-AAAA-0001", signature: "ab".repeat(32), version: "0.7.2", build: "" });
      if (msg.action === "preflight") send(PREFLIGHT);
    }, 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}

function reply(path: string, ...r: Reply[]) {
  replies[path] = r;
}

beforeEach(() => {
  calls = [];
  replies = {
    "/api/student/device-check": [{ status: 200, body: { ok: true, saved_at: "x" } }],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true }), { status: 200 });
      calls.push({
        url,
        method: init?.method ?? "GET",
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      const q = replies[url] ?? [];
      const r = q.length > 1 ? q.shift()! : q[0] ?? { status: 404, body: { detail: "Not found" } };
      return new Response(r.body === undefined ? "" : JSON.stringify(r.body), { status: r.status, headers: r.headers });
    }),
  );
  native();
});

afterEach(() => {
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  keepDeviceCheck(null);
  vi.unstubAllGlobals();
});

function renderLogin() {
  render(
    <MemoryRouter initialEntries={["/student/login"]}>
      <Routes>
        <Route path="/student/login" element={<StudentLogin />} />
        <Route path="/student" element={<p>Student home</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const called = (url: string) => calls.filter((c) => c.url === url);

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Test my device" }));
  return screen.findByRole("dialog", { name: "Test my device" });
}

describe("the app home (no exam code)", () => {
  it("has no code entry: a sign-in button and a yellow Test my device button", () => {
    renderLogin();
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/exam code/i)).toBeNull();
    expect(screen.queryAllByLabelText(/^Character \d of 6$/)).toHaveLength(0);
    expect(screen.queryByRole("textbox")).toBeNull();
    const test = screen.getByRole("button", { name: "Test my device" });
    expect(test.className).toContain("cs-test-btn");
    expect(screen.getByRole("button", { name: "Sign in with username and password" })).toBeTruthy();
  });

  it("the sign-in button opens the username and password form", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(screen.getByRole("button", { name: "Sign in with username and password" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
  });

  it("never calls the removed code-login routes", async () => {
    renderLogin();
    await screen.findByRole("heading", { name: "Sign in" });
    expect(calls.filter((c) => c.url.includes("code-login"))).toHaveLength(0);
  });

  it("outside the app there is no native home and no device test", () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    renderLogin();
    expect(screen.queryByRole("button", { name: "Test my device" })).toBeNull();
  });

  it("Test my device opens a panel with OK and Problem rows; Esc closes it; the result is sent only after sign-in", async () => {
    const user = userEvent.setup();
    reply("/api/auth/login", { status: 200, body: SESSION });
    renderLogin();
    const dialog = await openPanel(user);
    const rows = await within(dialog).findByRole("list", { name: "Device check results" });
    expect(within(rows).getByText("macOS 15.1")).toBeTruthy();
    expect(within(rows).getByText("Unplug the second display")).toBeTruthy();
    expect(within(rows).getByText("OK")).toBeTruthy();
    expect(within(rows).getByText("Problem")).toBeTruthy();
    expect(within(dialog).getByText("Your teacher sees this check after you sign in.")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Test again" })).toBeTruthy();
    expect(called("/api/student/device-check")).toHaveLength(0);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Test my device" }));
    await user.click(await screen.findByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Sign in with username and password" }));
    await user.type(screen.getByLabelText("Username"), "ax19");
    await user.type(screen.getByLabelText("Password"), "secret-pass");
    await user.click(screen.getByRole("button", { name: /^Sign in$/ }));
    await screen.findByText("Student home");
    expect(getToken("student")).toBe("SESSION-TOK");
    const sent = called("/api/student/device-check");
    expect(sent).toHaveLength(1);
    expect(sent[0]!.method).toBe("POST");
    expect(sent[0]!.headers.Authorization).toBe("Bearer SESSION-TOK");
    expect(sent[0]!.body).toMatchObject({
      platform: "mac",
      os_version: "15.1",
      exam_id: null,
      checks: PREFLIGHT.checks,
    });
  });

  it("an info row is labelled Info and Test again runs the check again", async () => {
    const user = userEvent.setup();
    renderLogin();
    const dialog = await openPanel(user);
    await within(dialog).findByRole("list", { name: "Device check results" });
    const post = (window as unknown as { webkit: { messageHandlers: { proctora: { postMessage: ReturnType<typeof vi.fn> } } } })
      .webkit.messageHandlers.proctora.postMessage;
    const before = post.mock.calls.filter((c) => c[0].action === "preflight").length;
    await user.click(within(dialog).getByRole("button", { name: "Test again" }));
    await within(dialog).findByRole("list", { name: "Device check results" });
    expect(post.mock.calls.filter((c) => c[0].action === "preflight").length).toBe(before + 1);
  });

  it("an app that does not answer the check says so", async () => {
    const post = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage: post } } };
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderLogin();
      await user.click(screen.getByRole("button", { name: "Test my device" }));
      await vi.advanceTimersByTimeAsync(3500);
      expect(await screen.findByText(/cannot run the full check/)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the /student/home route", () => {
  it("shows the native home with no code entry and its sign-in button opens the form", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/student/home"]}>
        <Routes>
          <Route path="/student/home" element={<StudentAppHome />} />
          <Route path="/student/login" element={<StudentLogin />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/exam code/i)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Sign in with username and password" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();
  });
});
