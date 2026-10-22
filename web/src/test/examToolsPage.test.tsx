// 2026-10-23 (exam tools) on the exam page and "Before you start": the tools the teacher
// allowed (and only those), the lock carries allowed_sites, panes open from More.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    materialsApi: {
      list: vi.fn(),
      upload: vi.fn(),
      remove: vi.fn(),
      attemptFiles: vi.fn(),
      attemptFile: vi.fn(),
    },
  };
});

import { materialsApi, setToken, studentApi } from "../api/client";
import * as bridge from "../lib/bridge";
import { _resetMaterialsForTests } from "../lib/materials";
import BeforeStartPage from "../pages/student/BeforeStartPage";
import ExamTakingPage from "../pages/student/ExamTakingPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const mat = materialsApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
const ALL: ExamTools = { calculator: "graphing", open_book: true, websites: ["docs.python.org", "en.cppreference.com"] };

function nativeApp() {
  const postMessage = vi.fn((msg: { action: string }) => {
    setTimeout(() => {
      if (msg.action === "preflight") send({ type: "preflight", problems: [] });
      if (msg.action === "lock") send({ type: "locked" });
    }, 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
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
    questions: [
      { id: "q1", type: "mcq", prompt: "2+2?", points: 1, options: ["3", "4"] },
      { id: "q2", type: "numeric", prompt: "pi?", points: 1, options: [] },
    ],
    answers: {},
    status: "in_progress",
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
  mat.attemptFiles.mockResolvedValue({
    files: [{ id: "m_1", name: "notes.txt", kind: "txt", media_type: "text/plain", size: 9, created_at: "" }],
  });
  mat.attemptFile.mockResolvedValue(new Blob(["F = ma"], { type: "text/plain" }));
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  _resetMaterialsForTests();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

describe("exam page tools", () => {
  it("no tools: no Calculator, no Materials / Websites, allowed_sites []", async () => {
    const post = nativeApp();
    api.getAttempt.mockResolvedValue(view());
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Calculator" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.queryByRole("menuitem", { name: /Materials|Websites/ })).toBeNull();
    const lock = post.mock.calls.map((c) => c[0] as Record<string, unknown>).find((m) => m.action === "lock");
    expect(lock).toMatchObject({ action: "lock", attempt_id: "a_1", session: "sess-1", allowed_sites: [] });
  });

  it("all tools: calculator stays open across questions; panes from More; sites go to the app", async () => {
    const post = nativeApp();
    api.getAttempt.mockResolvedValue(view({ tools: ALL }));
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    const msgs = () => post.mock.calls.map((c) => c[0] as Record<string, unknown>);
    expect(msgs().find((m) => m.action === "lock")).toMatchObject({ allowed_sites: ALL.websites });
    await waitFor(() =>
      expect(msgs().filter((m) => m.action === "session").slice(-1)[0]).toMatchObject({ allowed_sites: ALL.websites }),
    );

    // calculator
    fireEvent.click(screen.getByRole("button", { name: "Calculator" }));
    expect(screen.getByRole("dialog", { name: "Calculator" })).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByRole("tab", { name: "Graph" })).toBeInTheDocument();          // graphing mode
    fireEvent.change(screen.getByLabelText("Expression"), { target: { value: "6*7" } });
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByRole("heading", { name: "pi?" })).toBeInTheDocument();
    expect((screen.getByLabelText("Expression") as HTMLInputElement).value).toBe("6*7");
    fireEvent.click(screen.getByRole("button", { name: "Close calculator" }));
    expect(document.querySelector(".stu-calc")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Calculator" })).toHaveFocus();

    // materials (fetched when the exam opened: the count is in the menu)
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(await screen.findByRole("menuitem", { name: /Materials\s*1 file/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: /Materials/ }));
    expect(screen.getByRole("complementary", { name: "Materials" })).toBeInTheDocument();
    expect(document.querySelector(".stu-exam")).toHaveClass("has-pane");
    expect(screen.getByRole("heading", { name: "pi?" })).toBeInTheDocument();         // question stays
    fireEvent.click(await screen.findByRole("button", { name: /notes\.txt/ }));
    expect(await screen.findByText("F = ma")).toBeInTheDocument();

    // websites: hidden until asked for
    expect(document.querySelector("iframe")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Websites\s*2 sites/ }));
    expect(screen.getByRole("complementary", { name: "Websites" })).toBeInTheDocument();
    expect(document.querySelector("iframe")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "docs.python.org" }));
    expect(document.querySelector("iframe")?.getAttribute("src")).toBe("https://docs.python.org/");
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(document.querySelector(".stu-exam")).not.toHaveClass("has-pane");
  });

  it("an older server (no tools field) shows no tools", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view({ tools: undefined }));
    renderExam();
    expect(await screen.findByRole("heading", { name: "2+2?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Calculator" })).toBeNull();
    expect(mat.attemptFiles).not.toHaveBeenCalled();
  });
});

describe("before you start: tools and materials", () => {
  const EXAM = {
    id: "e_1", title: "Midterm", course: "", duration_min: 60, question_count: 5, status: "live",
    room: "", scheduled_at: null, attempt: null,
  };
  function renderStart() {
    return render(
      <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
        <Routes>
          <Route path="/student/exams/:examId/start" element={<BeforeStartPage />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("shows the allowed tools and the upload area for open book", async () => {
    api.listExams.mockResolvedValue([{ ...EXAM, tools: ALL }]);
    mat.list.mockResolvedValue({
      open_book: true, can_change: true, reason: null, files: [], used_bytes: 0, max_files: 5,
      max_bytes: 50 * 1024 * 1024, types: [],
    });
    renderStart();
    expect(await screen.findByTestId("start-tools")).toHaveTextContent(
      "Calculator (graphing) · Materials (open book) · 2 websites",
    );
    expect(await screen.findByTestId("materials-upload")).toBeInTheDocument();
    expect(mat.list).toHaveBeenCalledWith("e_1");
  });

  it("no upload area for a closed-book exam", async () => {
    api.listExams.mockResolvedValue([{ ...EXAM, tools: { calculator: "off", open_book: false, websites: [] } }]);
    renderStart();
    expect(await screen.findByRole("heading", { name: "Midterm" })).toBeInTheDocument();
    expect(screen.queryByTestId("materials-upload")).toBeNull();
    expect(screen.queryByTestId("start-tools")).toBeNull();
  });
});
