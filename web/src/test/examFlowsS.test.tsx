// S3 / S5 (2026-10-20) on the exam page: time-up seal flow, waiting for the teacher.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AttemptView } from "../api/types";

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

import { NetworkError, studentApi } from "../api/client";
import * as bridge from "../lib/bridge";
import ExamTakingPage from "../pages/student/ExamTakingPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

/** A Mac app that answers preflight / lock / seal like the real one. */
function nativeApp() {
  const postMessage = vi.fn((msg: { action: string; attempt_id?: string }) => {
    setTimeout(() => {
      if (msg.action === "preflight") send({ type: "preflight", problems: [] });
      if (msg.action === "lock") send({ type: "locked" });
      if (msg.action === "seal") send({ type: "sealed", attempt_id: msg.attempt_id, ok: true });
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
    questions: [{ id: "q1", type: "mcq", prompt: "2+2?", points: 1, options: ["3", "4"] }],
    answers: { q1: 1 },
    status: "in_progress",
    ...over,
  };
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

describe("exam page S3 / S5", () => {
  beforeEach(() => {
    api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at });
    api.saveAnswers.mockImplementation(async (_a: string, _x: unknown, seq: number) => ({ saved: true, client_seq: seq, server_time: "" }));
    api.events.mockResolvedValue({ ok: true, stored: 0 });
  });
  afterEach(() => {
    delete (window as unknown as { webkit?: unknown }).webkit;
    bridge._resetBridgeForTests();
    vi.clearAllMocks();
  });

  it("time up while offline: three choices; submit later seals the exact answers", async () => {
    const post = nativeApp();
    const past = new Date(Date.now() - 1000).toISOString();
    api.getAttempt.mockResolvedValue(view({ deadline_at: past }));
    api.heartbeat.mockRejectedValue(new NetworkError());
    api.submit.mockRejectedValue(new NetworkError());
    renderExam();
    expect(await screen.findByTestId("time-up-offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again now" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask the teacher for help" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Submit later from this computer" }));
    expect(await screen.findByTestId("time-up-sealed")).toBeInTheDocument();
    const seal = post.mock.calls.map(([m]) => m).find((m) => m.action === "seal") as unknown as {
      attempt_id: string; answers_json: string; client_seq: number;
    };
    expect(seal.attempt_id).toBe("a_1");
    expect(JSON.parse(seal.answers_json)).toEqual({ q1: 1 });
    expect(typeof seal.client_seq).toBe("number");
  });

  it("back after FairPlay closed: waits for the teacher, then shows the questions", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view({ approval: "pending" }));
    let approve: (v: unknown) => void = () => undefined;
    api.heartbeat.mockImplementation(() => new Promise((resolve) => (approve = resolve)));
    renderExam();
    expect(await screen.findByTestId("wait-for-teacher")).toHaveTextContent("Wait for your teacher");
    expect(screen.queryByText("2+2?")).toBeNull();
    approve({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at, approval: "approved" });
    expect(await screen.findByText("2+2?")).toBeInTheDocument();
    expect(screen.queryByTestId("wait-for-teacher")).toBeNull();
  });

  it("the start response's needs_approval also makes the page wait", async () => {
    nativeApp();
    api.getAttempt.mockResolvedValue(view({ needs_approval: true, approval: "pending" }));
    api.heartbeat.mockImplementation(() => new Promise(() => undefined));
    renderExam();
    expect(await screen.findByTestId("wait-for-teacher")).toBeInTheDocument();
  });

  it("DKU OJ changes the exam's name or tools while the page is open: the heartbeat's content_rev makes it load them again, answers kept", async () => {
    nativeApp();
    const changed = view({ content_rev: "r2", title: "Renamed by DKU OJ" });
    api.getAttempt.mockResolvedValueOnce(view({ content_rev: "r1" })).mockResolvedValue(changed);
    api.heartbeat.mockResolvedValue({
      ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at, content_rev: "r2",
    });
    renderExam();
    expect((await screen.findAllByText("Renamed by DKU OJ", undefined, { timeout: 4000 })).length).toBeGreaterThan(0);
    expect(api.getAttempt.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(api.saveAnswers).not.toHaveBeenCalledWith("a_1", expect.objectContaining({ q1: null }), expect.anything());
  });

});
