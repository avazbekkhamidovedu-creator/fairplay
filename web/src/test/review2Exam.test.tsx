// 2026-10-23 review (second pass) on the exam page: a manual hand-in still retrying when
// the time runs out turns into the time-up hand-in; the side panel's width follows the
// window; the question list keeps its header while it scrolls.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttemptView, ExamTools } from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
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

import { NetworkError, setToken, studentApi } from "../api/client";
import * as bridge from "../lib/bridge";
import { setRunnerForTests } from "../lib/code/runners";
import ExamTakingPage from "../pages/student/ExamTakingPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
const TOOLS: ExamTools = { calculator: "scientific", open_book: false, websites: ["docs.python.org"] };

function nativeApp() {
  const postMessage = vi.fn((msg: { action: string }) => {
    setTimeout(() => {
      if (msg.action === "preflight") send({ type: "preflight", problems: [] });
      if (msg.action === "lock") send({ type: "locked" });
    }, 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
}

function view(over: Partial<AttemptView> = {}): AttemptView {
  const now = Date.now();
  return {
    attempt_id: "a_1",
    exam_id: "e_1",
    title: "Quiz",
    duration_min: 30,
    started_at: new Date(now - 60_000).toISOString(),
    deadline_at: new Date(now + 30 * 60_000).toISOString(),
    server_time: new Date(now).toISOString(),
    questions: [{ id: "q1", type: "mcq", prompt: "2+2?", points: 1, options: ["3", "4"] }],
    answers: {},
    status: "in_progress",
    tools: TOOLS,
    ...over,
  } as AttemptView;
}

function renderExam() {
  return render(
    <MemoryRouter initialEntries={["/student/exam/a_1"]}>
      <Routes>
        <Route path="/student/exam/:attemptId" element={<ExamTakingPage />} />
        <Route path="/student/done/:attemptId" element={<p>done page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  setToken("student", "S");
  localStorage.setItem("fairplay:session:a_1", "sess-1");
  api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at });
  api.saveAnswers.mockImplementation(async (_a: string, _x: unknown, seq: number) => ({ saved: true, client_seq: seq, server_time: "" }));
  api.events.mockResolvedValue({ ok: true, stored: 0 });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  setRunnerForTests("python", null);
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

describe("H1: time runs out while a manual hand-in retries offline", () => {
  it("shows the time-up screen (retry / seal / help) and keeps trying as the time-up hand-in", async () => {
    nativeApp();
    const deadline_at = new Date(Date.now() + 2500).toISOString();
    api.getAttempt.mockResolvedValue(view({ deadline_at }));
    api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at });
    api.submit.mockRejectedValue(new NetworkError());
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review and submit" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit exam" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, submit" }));
    await waitFor(() => expect(api.submit).toHaveBeenCalled());
    expect(screen.queryByTestId("time-up-offline")).toBeNull();      // time left: still the review page
    expect(await screen.findByTestId("time-up-offline", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit later from this computer" })).toBeInTheDocument();
  }, 10000);
});

describe("M1: the side panel follows the window size", () => {
  it("is narrowed on a smaller window and gets its width back on a bigger one", async () => {
    nativeApp();
    Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
    api.getAttempt.mockResolvedValue(view());
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Websites/ }));
    const pane = () => screen.getByRole("complementary", { name: "Websites" });
    expect(pane().style.width).toBe("520px");
    Object.defineProperty(window, "innerWidth", { value: 820, configurable: true });
    act(() => {
      window.dispatchEvent(new Event("orientationchange"));
    });
    expect(pane().style.width).toBe("460px");   // the question keeps 360 px
    Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    expect(pane().style.width).toBe("520px");
    Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
  });
});

describe("L3 / M2: question list and bottom bar", () => {
  it("the list's title and legend sit in the sticky top; Review has a short label", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view());
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    const review = screen.getByRole("button", { name: "Review and submit" });
    expect(review.querySelector(".stu-lbl-short")).toHaveTextContent("Review");
    fireEvent.click(screen.getByRole("button", { name: "Question 1 of 1" }));
    const top = document.querySelector(".stu-navigator-top");
    expect(top).not.toBeNull();
    expect(top!.querySelector("h2")).toHaveTextContent("Quiz: questions");
    expect(top!.querySelector(".stu-legend")).not.toBeNull();
  });
});
