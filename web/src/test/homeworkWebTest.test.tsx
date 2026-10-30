import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as bridge from "../lib/bridge";
import { clearHomeworkSession, setHomeworkSession } from "../lib/homeworkSession";
import HomeworkPage from "../pages/student/HomeworkPage";

type Reply = { status: number; body?: unknown };

const OPEN = {
  state: "W".repeat(43),
  start_url: "https://site.example/secret-start",
  allowed_hosts: ["site.example"],
  finish_url: "https://site.example/finish",
  resume_paths: ["/a"],
  expires_at: new Date(Date.now() + 600_000).toISOString(),
};
const TIMES = { expires_at: new Date(Date.now() + 1_800_000).toISOString(), absolute_expires_at: new Date(Date.now() + 14_400_000).toISOString() };

let replies: Record<string, Reply>;
let calls: { url: string; init?: RequestInit }[];
let posted: { action: string; [k: string]: unknown }[];

const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

function native(homeworkLogin: boolean) {
  const postMessage = vi.fn((msg: { action: string }) => {
    posted.push(msg);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  bridge.onNative(() => undefined);
  send({ type: "state", locked: false, trusted: true, version: "1", ...(homeworkLogin ? { homeworkLogin: true } : {}) });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/student/homework"]}>
      <Routes>
        <Route path="/student/login" element={<p>login page</p>} />
        <Route path="/student/homework" element={<HomeworkPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const opens = () => posted.filter((m) => m.action === "openHomeworkLogin");

beforeEach(() => {
  calls = [];
  posted = [];
  replies = {
    "/api/homework/me": { status: 200, body: { name: "Ava K", netid: "ak1" } },
    "/api/homework/webtest": { status: 200, body: { sites: [{ id: "a", label: "Practice site" }, { id: "b", label: "Other site" }] } },
    "/api/homework/webtest/open": { status: 200, body: OPEN },
  };
  localStorage.clear();
  bridge._resetBridgeForTests();
  clearHomeworkSession();
  setHomeworkSession("HW1", TIMES);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
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

describe("web view test section", () => {
  it("is hidden when the list is empty", async () => {
    native(true);
    replies["/api/homework/webtest"] = { status: 200, body: { sites: [] } };
    renderPage();
    await screen.findByText("Welcome, Ava K");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/homework/webtest")).toBe(true));
    expect(screen.queryByText("Try the web view")).toBeNull();
    expect(screen.queryByText(/needs the FairPlay app/)).toBeNull();
  });

  it("is hidden when the list is empty in a browser", async () => {
    replies["/api/homework/webtest"] = { status: 200, body: { sites: [] } };
    renderPage();
    await screen.findByText("Welcome, Ava K");
    expect(screen.queryByText(/needs the FairPlay app/)).toBeNull();
  });

  it("shows the app hint in a browser when sites exist", async () => {
    renderPage();
    expect(await screen.findByText("The web view test needs the FairPlay app.")).toBeTruthy();
    expect(screen.queryByText("Try the web view")).toBeNull();
    expect(screen.queryByRole("button", { name: "Practice site" })).toBeNull();
  });

  it("shows the app hint when the app lacks homework windows", async () => {
    native(false);
    renderPage();
    expect(await screen.findByText("The web view test needs the FairPlay app.")).toBeTruthy();
  });

  it("lists the sites with the bearer token and 44px buttons", async () => {
    native(true);
    renderPage();
    expect(await screen.findByText("Try the web view")).toBeTruthy();
    expect(screen.getByText(/Nothing here is graded and nothing locks your computer/)).toBeTruthy();
    const b = screen.getByRole("button", { name: "Practice site" });
    expect(screen.getByRole("button", { name: "Other site" })).toBeTruthy();
    expect(b.style.minHeight).toBe("44px");
    const get = calls.find((c) => c.url === "/api/homework/webtest");
    expect((get?.init?.headers as Record<string, string>).Authorization).toBe("Bearer HW1");
  });

  it("sends exactly the server payload through the bridge and disables buttons while open", async () => {
    native(true);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    await waitFor(() => expect(opens()).toHaveLength(1));
    const post = calls.find((c) => c.url === "/api/homework/webtest/open");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ id: "a" });
    expect((post?.init?.headers as Record<string, string>).Authorization).toBe("Bearer HW1");
    expect(opens()[0]).toEqual({
      action: "openHomeworkLogin",
      state: OPEN.state,
      start_url: OPEN.start_url,
      allowed_hosts: OPEN.allowed_hosts,
      finish_url: OPEN.finish_url,
      resume_paths: OPEN.resume_paths,
    });
    expect((screen.getByRole("button", { name: "Practice site" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Other site" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.innerHTML).not.toContain("secret-start");
    expect(document.body.innerHTML).not.toContain("site.example");
  });

  it("re-enables silently after a cancelled result", async () => {
    native(true);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    await waitFor(() => expect(opens()).toHaveLength(1));
    send({ type: "homeworkLoginResult", state: OPEN.state, cancelled: true });
    await waitFor(() => expect((screen.getByRole("button", { name: "Practice site" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a calm message on an error result", async () => {
    native(true);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    await waitFor(() => expect(opens()).toHaveLength(1));
    send({ type: "homeworkLoginResult", state: OPEN.state, error: "https://site.example/boom" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("The web view could not be opened. Try again.");
    expect((screen.getByRole("button", { name: "Practice site" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("ignores a ticket result", async () => {
    native(true);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    await waitFor(() => expect(opens()).toHaveLength(1));
    send({ type: "homeworkLoginResult", state: OPEN.state, ticket: "T1" });
    await new Promise((r) => setTimeout(r, 20));
    expect((screen.getByRole("button", { name: "Practice site" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.url === "/api/homework/login/finish")).toBe(false);
  });

  it("shows the rate message on 429 and does not open a window", async () => {
    native(true);
    replies["/api/homework/webtest/open"] = { status: 429 };
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too many tries. Wait a moment and try again.");
    expect(opens()).toHaveLength(0);
    expect((screen.getByRole("button", { name: "Practice site" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("signs out on a 401 from the list", async () => {
    native(true);
    replies["/api/homework/webtest"] = { status: 401 };
    renderPage();
    expect(await screen.findByText("login page")).toBeTruthy();
  });

  it("signs out on a 401 from open", async () => {
    native(true);
    replies["/api/homework/webtest/open"] = { status: 401 };
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Practice site" }));
    expect(await screen.findByText("login page")).toBeTruthy();
  });
});
