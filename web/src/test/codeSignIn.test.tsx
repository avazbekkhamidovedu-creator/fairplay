import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getToken } from "../api/client";
import type { StudentExamSummary } from "../api/types";
import { CodeExamStart } from "../components/student/CodeExamStart";
import { UpcomingExamCard } from "../components/student/ExamCard";
import * as bridge from "../lib/bridge";
import { keepDeviceCheck } from "../lib/pendingDeviceCheck";
import StudentLogin from "../pages/student/StudentLogin";
import { isWaiting, upcomingExams } from "../pages/student/useStudentExams";

type Reply = { status: number; body?: unknown; headers?: Record<string, string> };
type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

let calls: Call[];
let replies: Record<string, Reply[]>;

const PROMPT = {
  first_name: "Avazbek",
  last_name: "Khamidov",
  netid: "ax19",
  exam_title: "CS 101 Midterm",
  confirm_token: "CONFIRM-TOK",
};
const SESSION = { token: "SESSION-TOK", role: "student", name: "Avazbek Khamidov" };
const NOT_VALID = { status: 400, body: { detail: "That code is not valid." } };
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
    "/api/student/code-login/challenge": [{ status: 200, body: { nonce: "NONCE-12345678", expires_in: 120 } }],
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

describe("student sign-in with an access code", () => {
  it("first screen is the code box and Test my device; the account form is one click away", async () => {
    const user = userEvent.setup();
    renderLogin();
    expect(screen.getByLabelText("Your code")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Test my device" })).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Use a username and password instead" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
  });

  it("outside the app there is no code screen and no device test", () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    renderLogin();
    expect(screen.queryByLabelText("Your code")).toBeNull();
    expect(screen.queryByRole("button", { name: "Test my device" })).toBeNull();
  });

  it("a wrong code shows the same generic message and stores nothing", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", NOT_VALID);
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "k7m-2qx{Enter}");
    expect((await screen.findByRole("alert")).textContent).toBe("That code is not valid.");
    expect(getToken("student")).toBeNull();
    expect(screen.getByLabelText("Your code")).toBeTruthy();
  });

  it("a code that is too short is not sent", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "K7M{Enter}");
    expect((await screen.findByRole("alert")).textContent).toMatch(/6 letters and digits/);
    expect(called("/api/student/code-login")).toHaveLength(0);
  });

  it("sends the cleaned-up code with the app's signed proof", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "k7m-2qx{Enter}");
    await screen.findByTestId("code-who");
    const sent = called("/api/student/code-login")[0]!.body as { code: string; client: Record<string, string> };
    expect(sent.code).toBe("K7M2QX");
    expect(sent.client).toMatchObject({ device_id: "DEVICE-AAAA-0001", nonce: "NONCE-12345678" });
  });

  it("asks 'This is me' / 'Not me'; Not me goes back to the code box and keeps nothing", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "K7M2QX{Enter}");
    const who = await screen.findByTestId("code-who");
    expect(who.textContent).toBe("You are Avazbek Khamidov (ax19) — Exam: CS 101 Midterm");
    await user.click(screen.getByRole("button", { name: "Not me" }));
    expect((screen.getByLabelText("Your code") as HTMLInputElement).value).toBe("");
    expect(getToken("student")).toBeNull();
    expect(called("/api/student/code-login/confirm")).toHaveLength(0);
  });

  it("This is me confirms, stores the token like a normal login and opens the student home", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", { status: 200, body: SESSION });
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "K7M2QX{Enter}");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "This is me" }));
    await screen.findByText("Student home");
    expect(called("/api/student/code-login/confirm")[0]!.body).toEqual({ confirm_token: "CONFIRM-TOK" });
    expect(getToken("student")).toBe("SESSION-TOK");
  });

  it("an expired confirmation sends the student back to the code box with the reason", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", {
      status: 400,
      body: { detail: "This confirmation has expired. Enter your code again." },
    });
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "K7M2QX{Enter}");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "This is me" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/expired/);
    expect(screen.getByLabelText("Your code")).toBeTruthy();
    expect(getToken("student")).toBeNull();
  });

  it("too many tries says how long to wait", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", {
      status: 429,
      body: { detail: { message: "Too many tries.", code: "too_many_codes", retry_after: 42 } },
      headers: { "Retry-After": "42" },
    });
    renderLogin();
    await user.type(screen.getByLabelText("Your code"), "K7M2QX{Enter}");
    expect((await screen.findByRole("alert")).textContent).toMatch(/42 seconds/);
  });

  it("Test my device runs the preflight, shows the rows and sends them only after sign-in", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", { status: 200, body: SESSION });
    renderLogin();
    await user.click(screen.getByRole("button", { name: "Test my device" }));
    const rows = await screen.findByRole("list", { name: "Device check results" });
    expect(within(rows).getByText("macOS 15.1")).toBeTruthy();
    expect(within(rows).getByText("Unplug the second display")).toBeTruthy();
    expect(screen.getByText("Your teacher sees this check after you sign in.")).toBeTruthy();
    expect(called("/api/student/device-check")).toHaveLength(0);

    await user.type(screen.getByLabelText("Your code"), "K7M2QX{Enter}");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "This is me" }));
    await screen.findByText("Student home");
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

describe("waiting for the exam to open", () => {
  const exam = (over: Partial<StudentExamSummary> = {}): StudentExamSummary => ({
    id: "e_1",
    title: "CS 101 Midterm",
    duration_min: 90,
    question_count: null,
    status: "draft",
    course: "CS 101",
    room: "",
    scheduled_at: null,
    access_code: true,
    attempt: null,
    ...over,
  });

  it("an exam entered with a code that is not open yet is waiting, not startable", () => {
    expect(isWaiting(exam())).toBe(true);
    expect(isWaiting(exam({ status: "live" }))).toBe(false);
    expect(isWaiting(exam({ access_code: undefined }))).toBe(false);
    expect(upcomingExams([exam()]).map((e) => e.id)).toEqual(["e_1"]);
    expect(upcomingExams([exam({ access_code: undefined })])).toEqual([]);
    const open = exam({ id: "e_2", status: "live" });
    expect(upcomingExams([exam(), open]).map((e) => e.id)).toEqual(["e_2", "e_1"]);
  });

  it("the card says 'Waiting for the exam to open' and has no Check in link", () => {
    render(
      <MemoryRouter>
        <UpcomingExamCard exam={exam()} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Waiting for the exam to open")).toBeTruthy();
    expect(screen.getByTestId("waiting-to-open")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Check in" })).toBeNull();
  });

  it("once it is live the card offers Check in again", () => {
    render(
      <MemoryRouter>
        <UpcomingExamCard exam={exam({ status: "live" })} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Check in" })).toBeTruthy();
  });
});

describe("starting an exam entered with a code", () => {
  const VIEW = { attempt_id: "a_1", exam_id: "e_1", status: "in_progress", questions: [], answers: {}, deadline_at: "2030-01-01T00:00:00Z", server_time: "2030-01-01T00:00:00Z" };

  function renderStart() {
    render(
      <MemoryRouter initialEntries={["/start"]}>
        <Routes>
          <Route path="/start" element={<CodeExamStart examId="e_1" />} />
          <Route path="/student/exam/:aid" element={<p>Exam page</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("has no start code to type: one button starts with an empty code", async () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    const user = userEvent.setup();
    reply("/api/student/exams/e_1/start", { status: 200, body: VIEW });
    renderStart();
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    await screen.findByText("Exam page");
    expect(called("/api/student/exams/e_1/start")[0]!.body).toEqual({ start_code: "" });
  });

  it("before the teacher opens the exam it says so", async () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    const user = userEvent.setup();
    reply("/api/student/exams/e_1/start", { status: 409, body: { detail: "Exam is not open" } });
    renderStart();
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/not open right now/);
  });
});
