// 2026-10-23: Start exam -> "Before you start" -> lock -> start code (wrong code, countdown,
// leave and unlock). The bridge is the fake Mac app used by the other tests.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    studentApi: { listExams: vi.fn(), start: vi.fn() },
  };
});

import { ApiError, studentApi } from "../api/client";
import * as bridge from "../lib/bridge";
import BeforeStartPage from "../pages/student/BeforeStartPage";

const api = studentApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const EXAM = {
  id: "e_1",
  title: "Midterm",
  course: "MATH 201",
  duration_min: 60,
  question_count: 22,
  status: "live",
  room: "",
  scheduled_at: null,
  attempt: null,
};

function native() {
  const postMessage = vi.fn();
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return postMessage;
}
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
const box = (n: number) => screen.getByRole("textbox", { name: `Digit ${n} of 6` }) as HTMLInputElement;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
      <Routes>
        <Route path="/student/exams/:examId/start" element={<BeforeStartPage />} />
        <Route path="/student/exam/:aid" element={<p>exam page</p>} />
        <Route path="/student" element={<p>dashboard</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  api.listExams.mockResolvedValue([EXAM]);
});
afterEach(() => {
  vi.clearAllMocks();
  delete (window as unknown as { webkit?: unknown }).webkit;
  delete (window as unknown as { fairplayBattery?: unknown }).fairplayBattery;
  bridge._resetBridgeForTests();
});

describe("before you start", () => {
  it("needs the tick before it locks, then shows the code screen (browser: no lock)", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "MATH 201 · Midterm" })).toBeInTheDocument();
    expect(screen.getByText(/60 minutes · 22 questions/)).toBeInTheDocument();
    const go = screen.getByRole("button", { name: "Lock and continue" });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /I understand and I'm ready/ }));
    expect(go).not.toBeDisabled();
    fireEvent.click(go);
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    // not locked in a browser: plain "go back"
    fireEvent.click(screen.getByRole("button", { name: "Not ready? Go back" }));
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  it("asks to plug in when the battery is low", async () => {
    (window as unknown as { fairplayBattery: unknown }).fairplayBattery = { level: 0.12, charging: false };
    renderPage();
    expect(await screen.findByTestId("start-battery")).toHaveTextContent("Battery 12%. Plug in your charger before you start.");
  });

  it("in the app: locks first, a wrong code shakes and clears, the right code opens the exam", async () => {
    const post = native();
    const user = userEvent.setup();
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preflight", allowed_apps: [] }));
    send({ type: "preflight", problems: [], apps_to_close: [] });
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
    send({ type: "locked", prelock: true });
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    expect(screen.getByText("Locked")).toBeInTheDocument();

    api.start.mockRejectedValueOnce(new ApiError(403, "Wrong start code"));
    await user.click(box(1));
    await user.keyboard("111111");
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That code isn't right");
    expect(box(1).value).toBe("");
    expect(document.querySelector(".stu-shake")).not.toBeNull();

    api.start.mockResolvedValueOnce({ attempt_id: "a_1" });
    await user.click(box(1));
    await user.keyboard("482915");
    await user.click(screen.getByRole("button", { name: "Start exam" }));
    expect(await screen.findByText("exam page")).toBeInTheDocument();
  });

  it("in the app: 'Not ready? Leave and unlock' unlocks and goes home", async () => {
    const post = native();
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ action: "preflight" })));
    send({ type: "preflight", problems: [], apps_to_close: [] });
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
    send({ type: "locked", prelock: true });
    fireEvent.click(await screen.findByRole("button", { name: "Not ready? Leave and unlock" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
    send({ type: "unlocked" });
    expect(await screen.findByText("dashboard")).toBeInTheDocument();
  });

  it("in the app: a refused unlock keeps the code screen and says to ask the invigilator", async () => {
    const post = native();
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ action: "preflight" })));
    send({ type: "preflight", problems: [], apps_to_close: [] });
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
    send({ type: "locked", prelock: true });
    fireEvent.click(await screen.findByRole("button", { name: "Not ready? Leave and unlock" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "preunlock" }));
    send({ type: "preunlockRefused" });
    expect(await screen.findByText(/ask the invigilator/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
  });

  it("in the app: a failed lock offers Try again", async () => {
    const post = native();
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ action: "preflight" })));
    send({ type: "preflight", problems: [], apps_to_close: [] });
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
    send({ type: "lockFailed", reason: "accessibility" });
    expect(await screen.findByRole("heading", { name: "This computer could not lock" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("an older app that ignores prelock still reaches the code screen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const post = native();
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
    fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ action: "preflight" })));
    send({ type: "preflight", problems: [], apps_to_close: [] });
    await waitFor(() => expect(post).toHaveBeenCalledWith({ action: "prelock" }));
    vi.advanceTimersByTime(bridge.PRELOCK_SILENT_MS + 10);
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not ready? Go back" })).toBeInTheDocument();
    vi.useRealTimers();
  });
});
