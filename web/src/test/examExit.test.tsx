import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttemptView, StudentQuestion } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    auth: { login: vi.fn(), me: vi.fn().mockResolvedValue({ username: "alice1", name: "Alice Chen", role: "student" }) },
    studentApi: {
      listExams: vi.fn(),
      start: vi.fn(),
      getAttempt: vi.fn(),
      saveAnswers: vi.fn(),
      heartbeat: vi.fn(),
      events: vi.fn(),
      submit: vi.fn(),
    },
  };
});

import { ApiError, studentApi } from "../api/client";
import { ResumeExam } from "../components/student/ResumeExam";
import { START_SLOW_MS } from "../components/student/StartCodeForm";
import * as bridge from "../lib/bridge";
import { isExited, markExited, takeExitNotice, EXIT_NOTICE } from "../lib/examExit";
import ExamTakingPage, { LOCK_STUCK_MS } from "../pages/student/ExamTakingPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const send = (detail: unknown) => act(() => void window.dispatchEvent(new CustomEvent("proctora-native", { detail })));

const questions: StudentQuestion[] = [
  { id: "q1", type: "mcq", prompt: "Pick one", points: 1, options: ["3", "6"] },
  { id: "q2", type: "short", prompt: "Capital of France?", points: 1, options: [] },
];

function attempt(): AttemptView {
  const now = Date.now();
  return {
    attempt_id: "a_1",
    exam_id: "e_1",
    title: "Quiz",
    course: "MATH 201",
    duration_min: 30,
    started_at: new Date(now).toISOString(),
    deadline_at: new Date(now + 30 * 60_000).toISOString(),
    server_time: new Date(now).toISOString(),
    questions,
    answers: {},
    status: "in_progress",
  };
}

function renderExam() {
  return render(
    <MemoryRouter initialEntries={["/student/exam/a_1"]}>
      <Routes>
        <Route path="/student" element={<p>student home</p>} />
        <Route path="/student/exam/:attemptId" element={<ExamTakingPage />} />
        <Route path="/student/done/:attemptId" element={<p>done page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  api.getAttempt.mockResolvedValue(attempt());
  api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: attempt().deadline_at });
  api.saveAnswers.mockImplementation(async (_a: string, _ans: unknown, seq: number) => ({ saved: true, client_seq: seq, server_time: new Date().toISOString() }));
  api.listExams.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

describe("exit flow", () => {
  it("the exited message clears the exam, stops sending and goes home with a notice", async () => {
    const user = userEvent.setup();
    renderExam();
    expect((await screen.findAllByText("Pick one"))[0]).toBeInTheDocument();
    await user.click(screen.getByText("6"));
    const beats = api.heartbeat.mock.calls.length;

    send({ type: "exited", attempt_id: "a_1" });

    expect(await screen.findByText("student home")).toBeInTheDocument();
    expect(screen.queryByText("Pick one")).toBeNull();
    expect(screen.queryByText("6")).toBeNull();
    expect(isExited("a_1")).toBe(true);
    expect(takeExitNotice()).toBe(true);
    expect(EXIT_NOTICE).toBe("You left the exam. Ask your invigilator to let you back in.");
    // the offline copy of the answers stays on this device
    expect(JSON.stringify({ ...localStorage })).toContain("q1");
    await new Promise((r) => setTimeout(r, 50));
    expect(api.heartbeat.mock.calls.length).toBe(beats);
    expect(api.submit).not.toHaveBeenCalled();
  });

  it("ignores an exited message for another attempt", async () => {
    renderExam();
    expect((await screen.findAllByText("Pick one"))[0]).toBeInTheDocument();
    send({ type: "exited", attempt_id: "someone_else" });
    expect(screen.getAllByText("Pick one")[0]).toBeInTheDocument();
    expect(isExited("a_1")).toBe(false);
  });

  it("a 409 session-gone answer on a heartbeat sends the student home", async () => {
    api.heartbeat.mockRejectedValue(new ApiError(409, "Your exam session is gone", "session_gone"));
    renderExam();
    expect(await screen.findByText("student home")).toBeInTheDocument();
    expect(screen.queryByText("Pick one")).toBeNull();
    expect(isExited("a_1")).toBe(true);
  });

  it("a 403 exit answer on an answer save sends the student home", async () => {
    const user = userEvent.setup();
    api.saveAnswers.mockRejectedValue(new ApiError(403, "You exited the exam", "exited"));
    renderExam();
    expect((await screen.findAllByText("Pick one"))[0]).toBeInTheDocument();
    await user.click(screen.getByText("6"));
    expect(await screen.findByText("student home", undefined, { timeout: 5000 })).toBeInTheDocument();
  });

  it("opening an exited attempt asks for the start code, not the questions", async () => {
    markExited("a_1");
    renderExam();
    expect(await screen.findByText("Enter the start code to continue")).toBeInTheDocument();
    expect(api.getAttempt).not.toHaveBeenCalled();
  });
});

describe("resume after an exit", () => {
  function renderResume() {
    return render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<ResumeExam examId="e_1" />} />
          <Route path="/student/exam/:aid" element={<p>exam page</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }
  const box = (n: number) => screen.getByRole("textbox", { name: `Digit ${n} of 6` });

  it("Resume exam always opens the start code form and sends start_code", async () => {
    const user = userEvent.setup();
    markExited("a_1");
    api.start.mockResolvedValue({ ...attempt() });
    renderResume();
    expect(api.start).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Resume exam" }));
    await user.click(box(1));
    await user.keyboard("123456");
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(api.start).toHaveBeenCalledWith("e_1", "123456");
    expect(await screen.findByText("exam page")).toBeInTheDocument();
    expect(isExited("a_1")).toBe(false);
  });

  it("start_code_required shows the invigilator message and keeps the form open", async () => {
    const user = userEvent.setup();
    api.start.mockRejectedValue(new ApiError(403, "Start code required", "start_code_required"));
    renderResume();
    await user.click(screen.getByRole("button", { name: "Resume exam" }));
    await user.click(box(1));
    await user.keyboard("000000");
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the start code from your invigilator");
    expect(box(1)).toBeInTheDocument();
  });

  it("a start that never answers offers Retry instead of spinning for ever", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.start.mockReturnValue(new Promise(() => undefined));
    renderResume();
    fireEvent.click(screen.getByRole("button", { name: "Resume exam" }));
    fireEvent.change(box(1), { target: { value: "1" } });
    for (let i = 2; i <= 6; i++) fireEvent.change(box(i), { target: { value: String(i) } });
    fireEvent.click(screen.getByRole("button", { name: "Start exam" }));
    expect(api.start).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(START_SLOW_MS + 100);
    });
    const slow = screen.getByTestId("start-slow");
    expect(slow).toHaveTextContent("taking too long");
    fireEvent.click(slow.querySelector("button")!);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(api.start).toHaveBeenCalledTimes(2);
  });
});

describe("a lock that never finishes", () => {
  it("shows Retry after 15 s and locks again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    renderExam();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    // preflight answers late, then the app keeps saying it is still waiting
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    const locks = () => postMessage.mock.calls.filter((c) => c[0].action === "lock").length;
    expect(locks()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    send({ type: "lockPending", reason: "waiting" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LOCK_STUCK_MS - 12100 + 500);
    });
    const stuck = await screen.findByTestId("lock-stuck");
    expect(stuck).toHaveTextContent("taking too long");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(locks()).toBe(2);
  });
});

describe("bridge", () => {
  it("requestExitPrompt posts exitPrompt in the app and does nothing in a browser", () => {
    expect(bridge.requestExitPrompt()).toBe(false);
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    expect(bridge.requestExitPrompt()).toBe(true);
    expect(postMessage).toHaveBeenCalledWith({ action: "exitPrompt" });
  });
});
