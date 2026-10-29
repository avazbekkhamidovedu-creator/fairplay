import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getToken } from "../api/client";
import type { StudentExamSummary } from "../api/types";
import { CodeExamStart } from "../components/student/CodeExamStart";
import { UpcomingExamCard } from "../components/student/ExamCard";
import * as bridge from "../lib/bridge";
import { rememberResume, resumeInfo } from "../lib/resumeState";
import { keepDeviceCheck } from "../lib/pendingDeviceCheck";
import StudentLogin from "../pages/student/StudentLogin";
import { countdownText, opensInMs, startsMessage } from "../lib/earlyCode";
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
  rememberResume(false, "");
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

const boxes = () => screen.getAllByLabelText(/^Character \d of 6$/) as HTMLInputElement[];
const codeText = () => boxes().map((b) => b.value).join("");

async function enter(user: ReturnType<typeof userEvent.setup>, code: string) {
  await user.keyboard(code);
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Test my device" }));
  return screen.findByRole("dialog", { name: "Test my device" });
}

describe("student sign-in with an access code", () => {
  it("first screen is six code boxes and a yellow Test my device button", () => {
    renderLogin();
    expect(screen.getByRole("heading", { name: "Enter your exam code" })).toBeTruthy();
    expect(screen.getByText("The 6-character code on the sheet your professor gave you.")).toBeTruthy();
    expect(boxes()).toHaveLength(6);
    expect(document.activeElement).toBe(boxes()[0]);
    for (const b of boxes()) {
      expect(b.getAttribute("autocomplete")).toBe("off");
      expect(b.getAttribute("autocapitalize")).toBe("characters");
      expect(b.getAttribute("inputmode")).toBe("text");
    }
    const test = screen.getByRole("button", { name: "Test my device" });
    expect(test.className).toContain("cs-test-btn");
    expect(screen.queryByRole("button", { name: "Use a username and password instead" })).toBeNull();
  });

  it("the old username link shows only when the server lists legacy_student_ui", async () => {
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) =>
      url === "/api/health" ? new Response(JSON.stringify({ features: ["legacy_student_ui"] }), { status: 200 }) : base(url, init)));
    const user = userEvent.setup();
    renderLogin();
    await user.click(await screen.findByRole("button", { name: "Use a username and password instead" }));
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
  });

  it("outside the app there is no code screen and no device test", () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    renderLogin();
    expect(screen.queryAllByLabelText(/^Character \d of 6$/)).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Test my device" })).toBeNull();
  });

  it("typing upper-cases, advances, and Continue waits for six characters", async () => {
    const user = userEvent.setup();
    renderLogin();
    const go = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    await user.keyboard("k7m2q");
    expect(codeText()).toBe("K7M2Q");
    expect(document.activeElement).toBe(boxes()[5]);
    expect(go.disabled).toBe(true);
    await user.keyboard("x");
    expect(codeText()).toBe("K7M2QX");
    expect(go.disabled).toBe(false);
  });

  it("backspace clears and steps back; arrow keys move", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.keyboard("K7M");
    expect(document.activeElement).toBe(boxes()[3]);
    await user.keyboard("{Backspace}");
    expect(codeText()).toBe("K7");
    expect(document.activeElement).toBe(boxes()[2]);
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(boxes()[1]);
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(document.activeElement).toBe(boxes()[3]);
  });

  it("pasting the whole code, with spaces or dashes, fills the boxes", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.paste("k7m-2qx");
    expect(codeText()).toBe("K7M2QX");
    await user.click(boxes()[0]!);
    await user.paste(" ab c 123 ");
    expect(codeText()).toBe("ABC123");
  });

  it("a wrong code shows the same generic message in an alert, marks the boxes and stores nothing", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", NOT_VALID);
    renderLogin();
    await enter(user, "K7M2QX");
    expect((await screen.findByRole("alert")).textContent).toBe("That code is not valid.");
    expect(boxes()[0]!.getAttribute("aria-invalid")).toBe("true");
    expect(getToken("student")).toBeNull();
    expect(boxes()).toHaveLength(6);
  });

  it("a valid code for an exam that opens later shows the start time and a countdown instead of Continue", async () => {
    const user = userEvent.setup();
    const startsAt = new Date(Date.now() + 3 * 24 * 3600 * 1000 + 5 * 60 * 1000);
    reply("/api/student/code-login", { status: 400, body: { detail: "too_early", starts_at: startsAt.toISOString() } });
    renderLogin();
    await enter(user, "K7M2QX");
    const info = await screen.findByTestId("code-early");
    expect(info.textContent).toBe(startsMessage(startsAt, new Date()));
    expect(info.textContent).toMatch(/^Your exam starts \w{3}, \w{3} \d+ at .* Come back 30 minutes before\.$/);
    expect(info.getAttribute("role")).toBe("status");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(boxes()[0]!.getAttribute("aria-invalid")).not.toBe("true");
    expect(codeText()).toBe("K7M2QX");
    const btn = screen.getByRole("button", { name: /^You can start in / }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(called("/api/student/code-login/confirm")).toHaveLength(0);
    await user.click(boxes()[5]!);
    await user.keyboard("{Backspace}");
    expect(screen.queryByTestId("code-early")).toBeNull();
  });

  it("sends the cleaned-up code with the app's signed proof", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    renderLogin();
    await user.paste("k7m-2qx");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByTestId("code-who");
    const sent = called("/api/student/code-login")[0]!.body as { code: string; client: Record<string, string> };
    expect(sent.code).toBe("K7M2QX");
    expect(sent.client).toMatchObject({ device_id: "DEVICE-AAAA-0001", nonce: "NONCE-12345678" });
  });

  it("Is this your slip? shows the names, the typed code and the exam; Not me returns to empty boxes", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    renderLogin();
    await enter(user, "K7M2QX");
    expect(await screen.findByRole("heading", { name: "Is this your slip?" })).toBeTruthy();
    const slip = screen.getByTestId("code-who");
    expect(slip.textContent).toBe("Last nameKhamidovFirst nameAvazbekNetIDax19Access codeK7M2QX");
    expect(screen.getByText("Exam: CS 101 Midterm")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Not me" }));
    expect(codeText()).toBe("");
    expect(boxes()).toHaveLength(6);
    expect(getToken("student")).toBeNull();
    expect(called("/api/student/code-login/confirm")).toHaveLength(0);
  });

  it("Confirm and continue confirms, stores the token like a normal login and opens the student home", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", { status: 200, body: SESSION });
    renderLogin();
    await enter(user, "K7M2QX");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
    await screen.findByText("Student home");
    expect(called("/api/student/code-login/confirm")[0]!.body).toEqual({ confirm_token: "CONFIRM-TOK" });
    expect(getToken("student")).toBe("SESSION-TOK");
  });

  it("a student with an attempt in progress sees resume wording and the flag is carried on", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: { ...PROMPT, resume: true } });
    reply("/api/student/code-login/confirm", { status: 200, body: { ...SESSION, resume: true } });
    renderLogin();
    await enter(user, "K7M2QX");
    await screen.findByTestId("code-who");
    expect(screen.getByTestId("code-resume").textContent).toBe("Welcome back, Avazbek. Your answers are saved. Continue where you stopped.");
    expect(screen.queryByRole("button", { name: "Confirm and continue" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Resume exam" }));
    await screen.findByText("Student home");
    expect(resumeInfo()).toEqual({ name: "Avazbek Khamidov" });
  });

  it("without resume the confirm screen keeps its wording and nothing is carried on", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: { ...PROMPT, resume: false } });
    reply("/api/student/code-login/confirm", { status: 200, body: SESSION });
    renderLogin();
    await enter(user, "K7M2QX");
    await screen.findByTestId("code-who");
    expect(screen.queryByTestId("code-resume")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
    await screen.findByText("Student home");
    expect(resumeInfo()).toBeNull();
  });

  it("an expired confirmation sends the student back to the code boxes with the reason", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", {
      status: 400,
      body: { detail: "This confirmation has expired. Enter your code again." },
    });
    renderLogin();
    await enter(user, "K7M2QX");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/expired/);
    expect(boxes()).toHaveLength(6);
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
    await enter(user, "K7M2QX");
    expect((await screen.findByRole("alert")).textContent).toMatch(/42 seconds/);
  });

  it("Test my device opens a panel with OK and Problem rows; Esc closes it; the result is sent only after sign-in", async () => {
    const user = userEvent.setup();
    reply("/api/student/code-login", { status: 200, body: PROMPT });
    reply("/api/student/code-login/confirm", { status: 200, body: SESSION });
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

    await user.click(boxes()[0]!);
    await enter(user, "K7M2QX");
    await screen.findByTestId("code-who");
    await user.click(screen.getByRole("button", { name: "Confirm and continue" }));
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

  it("says Resume exam when the student is resuming", async () => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    const user = userEvent.setup();
    rememberResume(true, "Avazbek Khamidov");
    reply("/api/student/exams/e_1/start", { status: 200, body: VIEW });
    renderStart();
    expect(screen.queryByRole("button", { name: "Start exam" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Resume exam" }));
    await screen.findByText("Exam page");
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

describe("early code message", () => {
  const at = new Date(2026, 9, 30, 15, 0);

  it("today: only the time", () => {
    expect(startsMessage(at, new Date(2026, 9, 30, 9, 0), "en-US")).toBe(
      "Your exam starts at 3:00 PM. Come back 30 minutes before.",
    );
  });

  it("another day: weekday and date too", () => {
    expect(startsMessage(at, new Date(2026, 9, 29, 23, 59), "en-US")).toBe(
      "Your exam starts Fri, Oct 30 at 3:00 PM. Come back 30 minutes before.",
    );
  });

  it("counts down to 30 minutes before the start", () => {
    const ms = opensInMs(at, new Date(2026, 9, 30, 13, 59, 30));
    expect(ms).toBe(30 * 60 * 1000 + 30 * 1000);
  });

  it("formats minutes, seconds and hours; never negative", () => {
    expect(countdownText(754_000)).toBe("12:34");
    expect(countdownText(3_909_000)).toBe("1:05:09");
    expect(opensInMs(at, new Date(2026, 9, 30, 16, 0))).toBe(0);
  });
});
