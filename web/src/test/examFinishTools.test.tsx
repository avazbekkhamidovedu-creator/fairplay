// 2026-10-23 (review fixes) on the exam page: focus goes back to More when a side panel
// closes; no calculator / panel while handing in or after the time is up; the attempt's
// cross-outs / hidden timer / run results and the Python worker go when the exam finishes.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttemptView, ExamTools, StudentQuestion } from "../api/types";

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

import { setToken, studentApi } from "../api/client";
import { CodeQuestion, forgetCodeResults, rememberedCodeResults } from "../components/student/CodeQuestion";
import * as bridge from "../lib/bridge";
import { getRunner, setRunnerForTests } from "../lib/code/runners";
import type { Runner } from "../lib/code/runner";
import { TEXT_SIZE_KEY, clearExamPrefs } from "../lib/examPrefs";
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

async function openToolsAndReview() {
  expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Calculator" }));
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("menuitem", { name: /Websites/ }));
  expect(screen.getByRole("tab", { name: /Websites/ })).toBeInTheDocument();
  expect(document.querySelector(".stu-calc")).not.toBeNull();
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

describe("material tabs and focus", () => {
  it("closing a tab: focus on the question's tab, or back on More after the last one", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view());
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Websites/ }));
    fireEvent.click(screen.getByRole("button", { name: "docs.python.org" }));
    expect(screen.getByRole("tab", { name: /docs\.python\.org/ })).toHaveAttribute("aria-selected", "true");
    // the site took the list's tab; More opens the list again
    expect(screen.queryByRole("tab", { name: /Websites/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Websites/ }));
    fireEvent.click(screen.getByRole("tab", { name: /docs\.python\.org/ }));
    fireEvent.click(screen.getByRole("button", { name: "Close docs.python.org" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: /Question 1/ })).toHaveFocus());
    const tab = screen.getByRole("tab", { name: /Websites/ });
    // Delete on a focused tab closes it too
    fireEvent.click(tab);
    tab.focus();
    fireEvent.keyDown(tab, { key: "Delete" });
    expect(screen.queryByRole("tab", { name: /Websites/ })).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "More" })).toHaveFocus());
  });
});

describe("no tools while handing in", () => {
  it("hides the calculator and the panel while submitting", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view());
    let resolveSubmit: (v: unknown) => void = () => undefined;
    api.submit.mockReturnValue(new Promise((r) => (resolveSubmit = r)));
    renderExam();
    await openToolsAndReview();
    fireEvent.click(screen.getByRole("button", { name: "Review and submit" }));
    // layout D: the material tabs belong to the question screen; they come back with it
    fireEvent.click(screen.getByRole("button", { name: /Keep working|Back to questions/ }));
    expect(screen.getByRole("tab", { name: /Websites/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review and submit" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit exam" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, submit" }));
    await waitFor(() => expect(api.submit).toHaveBeenCalled());
    expect(screen.queryByRole("tab", { name: /Websites/ })).toBeNull();
    // 2026-10-23 review: hidden but kept (a failed hand-in doesn't lose its contents)
    expect(document.querySelector(".stu-calc.open")).toBeNull();
    expect(document.querySelector(".stu-calc")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByRole("button", { name: "Calculator" })).toBeNull();
    await act(async () => resolveSubmit({ receipt: "R", submitted_at: "" }));
    expect(await screen.findByText("done page")).toBeInTheDocument();
  });

  it("hides them when the time is up", async () => {
    nativeApp();
    const deadline_at = new Date(Date.now() + 1500).toISOString();
    api.getAttempt.mockResolvedValue(view({ deadline_at }));
    api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at });
    api.submit.mockReturnValue(new Promise(() => undefined));
    renderExam();
    await openToolsAndReview();
    await waitFor(() => expect(api.submit).toHaveBeenCalled(), { timeout: 4000 });
    expect(screen.queryByRole("tab", { name: /Websites/ })).toBeNull();
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.querySelector(".stu-calc.open")).toBeNull();
  });
});

describe("finishing forgets the attempt on this device", () => {
  it("removes cross-outs, the cross-out switch and the hidden timer (text size stays); stops the shared runners", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view());
    api.submit.mockResolvedValue({ receipt: "R", submitted_at: "" });
    localStorage.setItem("fairplay:crossed:a_1", JSON.stringify({ q1: [0] }));
    localStorage.setItem("fairplay:crossmode:a_1", "1");
    localStorage.setItem("fairplay:timerhidden:a_1", "1");
    localStorage.setItem("fairplay:crossed:a_other", JSON.stringify({ q1: [1] }));
    localStorage.setItem(TEXT_SIZE_KEY, "1.3");
    const sharedBefore = await getRunner("javascript");
    const dispose = vi.spyOn(sharedBefore!, "dispose");
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review and submit" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit exam" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, submit" }));
    expect(await screen.findByText("done page")).toBeInTheDocument();
    for (const k of ["fairplay:crossed:a_1", "fairplay:crossmode:a_1", "fairplay:timerhidden:a_1"]) expect(localStorage.getItem(k), k).toBeNull();
    expect(localStorage.getItem("fairplay:crossed:a_other")).not.toBeNull();
    expect(localStorage.getItem(TEXT_SIZE_KEY)).toBe("1.3");
    expect(dispose).toHaveBeenCalled();
    expect(await getRunner("javascript")).not.toBe(sharedBefore); // a new worker next exam
  });

  it("clearExamPrefs touches only that attempt", () => {
    localStorage.setItem("fairplay:timerhidden:x", "1");
    localStorage.setItem("fairplay:timerhidden:y", "1");
    clearExamPrefs("x");
    expect(localStorage.getItem("fairplay:timerhidden:x")).toBeNull();
    expect(localStorage.getItem("fairplay:timerhidden:y")).toBe("1");
  });
});

describe("coding run results are kept per attempt", () => {
  const q: StudentQuestion = {
    id: "q_code",
    type: "code",
    prompt: "Echo",
    points: 1,
    options: [],
    language: "python",
    starter_code: "",
    tests: [],
    write_only: false,
    time_limit_ms: 1000,
  } as unknown as StudentQuestion;
  const runner: Runner = {
    language: "python",
    prepare: () => Promise.resolve(),
    run: (_c, stdin) => Promise.resolve({ status: "ok", stdout: `got ${stdin}`, stderr: "", timeMs: 1 }),
    dispose: () => undefined,
  };
  const show = (aid: string) =>
    render(<CodeQuestion attemptId={aid} question={q} value={undefined} onChange={() => undefined} prompt={<h1>Echo</h1>} />);

  it("another attempt with the same question id does not see them; finishing forgets them", async () => {
    setRunnerForTests("python", runner);
    const a = show("a1");
    fireEvent.change(screen.getByLabelText("Input"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByTestId("custom-output")).toHaveTextContent("got hello");
    a.unmount();

    const b = show("a2");
    expect(screen.queryByTestId("custom-output")).toBeNull();
    expect((screen.getByLabelText("Input") as HTMLTextAreaElement).value).toBe("");
    b.unmount();

    const c = show("a1");
    expect(screen.getByTestId("custom-output")).toHaveTextContent("got hello"); // same attempt: kept
    c.unmount();

    const before = rememberedCodeResults();
    forgetCodeResults("a1");
    expect(rememberedCodeResults()).toBe(before - 1);
    show("a1");
    expect(screen.queryByTestId("custom-output")).toBeNull();
  });
});
