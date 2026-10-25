// 2026-10-23 review (second pass) on the exam page: a manual hand-in still retrying when
// the time runs out turns into the time-up hand-in; the side panel's width follows the
// window; the question list keeps its header while it scrolls.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import ExamTakingPage, { SPLITS } from "../pages/student/ExamTakingPage";

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

describe("layout D: the divider between question and answer", () => {
  it("moves with the keyboard and the pointer, and is remembered for this exam", async () => {
    nativeApp();
    SPLITS.clear();
    // jsdom has no PointerEvent: a MouseEvent with a pointerId is enough here
    class FakePointer extends MouseEvent {
      pointerId: number;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    }
    vi.stubGlobal("PointerEvent", FakePointer);
    api.getAttempt.mockResolvedValue(view());
    const { unmount } = renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    const card = screen.getByTestId("exam-card");
    const sep = screen.getByRole("separator", { name: "Resize the question and answer panes" });
    expect(sep).toHaveAttribute("aria-valuenow", "50");
    expect(card.style.getPropertyValue("--xd-split")).toBe("50%");
    fireEvent.keyDown(sep, { key: "ArrowLeft" });
    expect(sep).toHaveAttribute("aria-valuenow", "45");
    fireEvent.keyDown(sep, { key: "ArrowRight", shiftKey: true });
    expect(sep).toHaveAttribute("aria-valuenow", "55");
    fireEvent.keyDown(sep, { key: "End" });
    expect(sep).toHaveAttribute("aria-valuenow", "75");
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(sep).toHaveAttribute("aria-valuenow", "75");        // kept within limits
    fireEvent.keyDown(sep, { key: "Home" });
    expect(sep).toHaveAttribute("aria-valuenow", "25");
    // pointer: 600 px into a 1000 px wide card -> 60 %
    vi.spyOn(card, "getBoundingClientRect").mockReturnValue({ left: 0, width: 1000, top: 0, height: 600, right: 1000, bottom: 600, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.pointerDown(sep, { pointerId: 1, clientX: 250 });
    fireEvent.pointerMove(sep, { pointerId: 1, clientX: 600 });
    fireEvent.pointerUp(sep, { pointerId: 1 });
    expect(sep).toHaveAttribute("aria-valuenow", "60");
    expect(card.style.getPropertyValue("--xd-split")).toBe("60%");
    // remembered in memory for this exam (a reload of the page component keeps it)
    unmount();
    renderExam();
    expect(await screen.findByRole("separator", { name: "Resize the question and answer panes" })).toHaveAttribute("aria-valuenow", "60");
    expect(SPLITS.get("e_1:q")).toBe(60);
    vi.unstubAllGlobals();
  });

  it("coding: the problem in the left pane, the editor on the right; Wide editor moves the divider", async () => {
    nativeApp();
    SPLITS.clear();
    api.getAttempt.mockResolvedValue(
      view({
        questions: [
          { id: "c1", type: "code", prompt: "Write double(n)", points: 2, options: [], language: "python",
            tests: [{ input: "2", expected: "4" }] },
        ],
      } as Partial<AttemptView>),
    );
    renderExam();
    const problem = await screen.findByRole("region", { name: "Problem" });
    await waitFor(() => expect(problem.closest(".xd-left")).not.toBeNull());
    expect(screen.getByRole("region", { name: "Problem" })).toHaveTextContent("Write double(n)");
    expect(screen.getByRole("region", { name: "Your code" }).closest(".xd-right")).not.toBeNull();
    const sep = screen.getByRole("separator", { name: "Resize the question and answer panes" });
    expect(sep).toHaveAttribute("aria-valuenow", "42");             // code starts with more room for the editor
    const wide = screen.getByRole("button", { name: "Wide editor" });
    fireEvent.click(wide);
    expect(wide).toHaveAttribute("aria-pressed", "true");
    expect(sep).toHaveAttribute("aria-valuenow", "25");
    fireEvent.click(wide);
    expect(sep).toHaveAttribute("aria-valuenow", "42");
    expect(SPLITS.get("e_1:code")).toBe(42);
  });

  it("tools on the right, question on the left; Next becomes Review and submit on the last", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view());
    renderExam();
    const prompt = await screen.findByRole("heading", { name: "2+2?" });
    expect(prompt.closest(".xd-left")).not.toBeNull();
    const right = document.querySelector(".xd-right") as HTMLElement;
    expect(within(right).getByRole("checkbox", { name: /Mark for review/ })).toBeInTheDocument();
    expect(within(right).getByRole("heading", { name: "Choose the right option" })).toBeInTheDocument();
    expect(within(right).getAllByRole("radio").length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: "Question 1 of 1" })).toHaveTextContent(/1 of 1/);
    expect(screen.getByRole("button", { name: "Review and submit" })).toBeInTheDocument();
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
