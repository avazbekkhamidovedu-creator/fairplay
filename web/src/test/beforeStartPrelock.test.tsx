// 2026-10-23 (review fixes): "Before you start" and the pre-exam lock: a reload while locked
// goes to the code, Back unlocks a locked computer, a late lock answer is not lost.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, studentApi: { listExams: vi.fn(), start: vi.fn() } };
});

import { studentApi } from "../api/client";
import * as bridge from "../lib/bridge";
import BeforeStartPage from "../pages/student/BeforeStartPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const EXAM = { id: "e_1", title: "Midterm", course: "MATH 201", duration_min: 60, question_count: 22, status: "live", room: "", scheduled_at: null, attempt: null };

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
const state = (over: Record<string, unknown>) => send({ type: "state", locked: false, trusted: true, version: "1", ...over });

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
      <Routes>
        <Route path="/student/exams/:examId/start" element={<BeforeStartPage />} />
        <Route path="/student" element={<p>dashboard</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function toPrelockRequest(post: ReturnType<typeof vi.fn>) {
  fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
  fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ action: "preflight" })));
  send({ type: "preflight", problems: [], apps_to_close: [] });
  await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
}

beforeEach(() => {
  api.listExams.mockResolvedValue([EXAM]);
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
});

describe("before you start: the pre-exam lock", () => {
  it("the state message keeps prelock", () => {
    native();
    bridge.onNative(() => undefined);
    state({ locked: true, prelock: true });
    expect(bridge.getNativeState()?.prelock).toBe(true);
  });

  it("opened while the pre-exam lock is on: straight to the code, Leave unlocks", async () => {
    const post = native();
    bridge.onNative(() => undefined);
    state({ locked: true, prelock: true });
    renderPage();
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    expect(screen.getByText("Locked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Not ready? Leave and unlock" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
    send({ type: "unlocked" });
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  it("a state with prelock that arrives after the page opened jumps to the code", async () => {
    native();
    renderPage();
    expect(await screen.findByRole("heading", { name: "MATH 201 · Midterm" })).toBeInTheDocument();
    state({ locked: true, prelock: true });
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not ready? Leave and unlock" })).toBeInTheDocument();
  });

  it("Back on the rules screen unlocks first when the computer is locked", async () => {
    const post = native();
    bridge.onNative(() => undefined);
    state({ locked: true });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
    send({ type: "unlocked" });
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  it("Back on the rules screen just goes home when not locked", async () => {
    const post = native();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Back" }));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalledWith({ action: "preunlock" });
  });

  it("a late lock answer after the 4 s 'unsupported' guess still counts (iPad)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const post = native();
    renderPage();
    await toPrelockRequest(post);
    vi.advanceTimersByTime(bridge.PRELOCK_SILENT_MS + 10);
    expect(await screen.findByRole("button", { name: "Not ready? Go back" })).toBeInTheDocument();
    send({ type: "locked", prelock: true }); // Assessment Mode came on
    expect(await screen.findByRole("button", { name: "Not ready? Leave and unlock" })).toBeInTheDocument();
    expect(screen.getByText("Locked")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Not ready? Leave and unlock" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
  });

  it("leaving unlocks a locked computer even when the page thinks there is no pre-exam lock", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const post = native();
    renderPage();
    await toPrelockRequest(post);
    vi.advanceTimersByTime(bridge.PRELOCK_SILENT_MS + 10);
    fireEvent.click(await screen.findByRole("button", { name: "Not ready? Go back" }));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalledWith({ action: "preunlock" }); // not locked: nothing to undo
  });

  it("…and when a lock without the prelock flag arrived", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const post = native();
    renderPage();
    await toPrelockRequest(post);
    vi.advanceTimersByTime(bridge.PRELOCK_SILENT_MS + 10);
    await screen.findByRole("button", { name: "Not ready? Go back" });
    send({ type: "locked" });
    expect(bridge.isLocked()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Not ready? Go back" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
    send({ type: "unlocked" });
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });
});
