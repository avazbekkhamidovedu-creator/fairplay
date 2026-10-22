// 2026-10-22: the sample exam ("Try a sample exam"): dashboard card, the lock bar's
// "Stop practice", the stop flow (server first, then unlock, home only after the unlock)
// and the sample-only offline exit code.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AttemptView } from "../api/types";

const api = vi.hoisted(() => ({
  getAttempt: vi.fn(),
  heartbeat: vi.fn(),
  saveAnswers: vi.fn(),
  events: vi.fn(),
  submit: vi.fn(),
  trialInfo: vi.fn(),
  startTrial: vi.fn(),
  trialStop: vi.fn(),
  trialKey: vi.fn(),
}));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return { ...real, studentApi: { ...real.studentApi, ...api } };
});

import { ApiError, NetworkError } from "../api/client";
import { LockedBar } from "../components/student/LockedBar";
import { TrialCard } from "../components/student/TrialCard";
import * as bridge from "../lib/bridge";
import { hmacSha256, normalizeAppCode, offlineExitCode, sha256 } from "../lib/offlineExit";
import DonePage from "../pages/student/DonePage";
import ExamTakingPage from "../pages/student/ExamTakingPage";

const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
const enc = (s: string) => new TextEncoder().encode(s);
const send = (detail: unknown) => window.dispatchEvent(new CustomEvent("proctora-native", { detail }));

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  delete (window as unknown as { webkit?: unknown }).webkit;
  bridge._resetBridgeForTests();
  localStorage.clear();
  sessionStorage.clear();
});

function view(over: Partial<AttemptView> = {}): AttemptView {
  const now = Date.now();
  return {
    attempt_id: "a_t", exam_id: "e_sample_exam", title: "Sample exam", course: "", duration_min: 10,
    started_at: new Date(now).toISOString(), deadline_at: new Date(now + 10 * 60_000).toISOString(),
    server_time: new Date(now).toISOString(), status: "in_progress", answers: {}, mode: "questions",
    external_url: null, allowed_domains: [], finish_url_contains: null, allowed_apps: [],
    questions: [{ id: "q1", type: "mcq", prompt: "Which is prime?", points: 1, options: ["21", "29"] }],
    trial: true,
    ...over,
  } as AttemptView;
}

function Home() {
  const st = useLocation().state as { trialDone?: string } | null;
  return <p>home {st?.trialDone ?? ""}</p>;
}

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/student" element={<Home />} />
        <Route path="/student/exam/:attemptId" element={<ExamTakingPage />} />
        <Route path="/student/done/:attemptId" element={<DonePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function online() {
  api.heartbeat.mockResolvedValue({ ok: true, status: "in_progress", server_time: new Date().toISOString(), deadline_at: view().deadline_at });
  api.events.mockResolvedValue({ ok: true, stored: 0 });
  api.saveAnswers.mockResolvedValue({ saved: true, client_seq: 1, server_time: new Date().toISOString() });
}

/** A native host that answers preflight (nothing to fix) and lock, and records messages. */
function nativeHost() {
  const posted: Array<Record<string, unknown>> = [];
  const postMessage = vi.fn((msg: Record<string, unknown>) => {
    posted.push(msg);
    if (msg.action === "preflight") setTimeout(() => send({ type: "preflight", problems: [], apps_to_close: [] }), 0);
    if (msg.action === "lock") setTimeout(() => send({ type: "locked" }), 0);
  });
  (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
  return posted;
}

describe("offline exit code (sample exam only)", () => {
  it("SHA-256 and HMAC match the standard vectors", () => {
    expect(hex(sha256(enc("abc")))).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(hex(sha256(enc("")))).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    // RFC 4231 test case 2
    expect(hex(hmacSha256(enc("Jefe"), enc("what do ya want for nothing?")))).toBe(
      "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    );
  });

  it("matches the server's offline exit rule (CONTRACT test vector)", () => {
    const key = hex(Uint8Array.from({ length: 32 }, (_, i) => i));
    expect(offlineExitCode(key, "K7QM3XPA")).toBe("06710479");
    expect(offlineExitCode(key, "k7qm-3xpa")).toBe("06710479");
    expect(offlineExitCode(key, "K7QM3XP0")).toBeNull(); // 0 is never shown
    expect(offlineExitCode("zz", "K7QM3XPA")).toBeNull();
    expect(normalizeAppCode("ab cd-efgh")).toBe("ABCDEFGH");
  });
});

describe("sample exam card", () => {
  it("shows on the dashboard and starts the sample exam", async () => {
    api.trialInfo.mockResolvedValue({ exam_id: "e_sample_exam", title: "Sample exam", duration_min: 10, question_count: 4, attempt: null });
    api.startTrial.mockResolvedValue(view());
    online();
    render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<TrialCard />} />
          <Route path="/student/exam/:attemptId" element={<p>exam page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Sample exam" })).toBeTruthy();
    expect(screen.getByText("See how an exam works: FairPlay locks this computer just like on exam day.")).toBeTruthy();
    expect(await screen.findByText(/10 minutes · 4 questions/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try the sample exam" }));
    expect(await screen.findByText("exam page")).toBeTruthy();
    expect(api.startTrial).toHaveBeenCalledTimes(1);
  });

  it("offers Continue while one runs and explains a refused start", async () => {
    api.trialInfo.mockResolvedValue({ exam_id: "e", title: "Sample exam", duration_min: 10, question_count: 4, attempt: { attempt_id: "a", status: "in_progress" } });
    api.startTrial.mockRejectedValue(new ApiError(403, "Open this exam in the FairPlay app. It cannot be taken in a web browser."));
    render(
      <MemoryRouter>
        <TrialCard />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Continue the sample exam" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/FairPlay app/);
  });
});

describe("lock bar in a sample exam", () => {
  it("shows the label and Stop practice next to the timer", () => {
    const stop = vi.fn();
    const { rerender } = render(<LockedBar title="Sample exam" locked native sync="synced" remaining={600_000} trial={{ onStop: stop }} />);
    expect(screen.getByText("Sample exam", { selector: ".stu-trial-badge" })).toBeTruthy();
    expect(screen.getByRole("timer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop practice" }));
    expect(stop).toHaveBeenCalledTimes(1);
    rerender(<LockedBar title="Sample exam" locked native sync="synced" remaining={600_000} trial={{ onStop: stop, disabled: true }} />);
    expect((screen.getByRole("button", { name: "Stop practice" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a real exam has no Stop practice", async () => {
    api.getAttempt.mockResolvedValue(view({ trial: false, title: "Midterm" }));
    online();
    at("/student/exam/a_t");
    expect(await screen.findByText("Which is prime?")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop practice" })).toBeNull();
    expect(api.trialKey).not.toHaveBeenCalled();
  });
});

describe("Stop practice", () => {
  it("in a browser: stops on the server, then goes home with a note", async () => {
    api.getAttempt.mockResolvedValue(view());
    api.trialKey.mockResolvedValue({ key: "00".repeat(32) });
    api.trialStop.mockResolvedValue({ status: "submitted", submitted_at: "t" });
    online();
    at("/student/exam/a_t");
    fireEvent.click(await screen.findByRole("button", { name: "Stop practice" }));
    expect(await screen.findByText("home stopped")).toBeTruthy();
    expect(api.trialStop).toHaveBeenCalledWith("a_t");
    expect(api.submit).not.toHaveBeenCalled();
  });

  it("in the app: unlock only after the server stop, home only after the app unlocked", async () => {
    const posted = nativeHost();
    api.getAttempt.mockResolvedValue(view());
    api.trialKey.mockResolvedValue({ key: "00".repeat(32) });
    let release: (v: unknown) => void = () => undefined;
    api.trialStop.mockReturnValue(new Promise((r) => (release = r)));
    online();
    at("/student/exam/a_t");
    const stop = await screen.findByRole("button", { name: "Stop practice" });
    await waitFor(() => expect(posted.some((m) => m.action === "lock")).toBe(true));
    await screen.findByText("Which is prime?");
    fireEvent.click(stop);
    await waitFor(() => expect(api.trialStop).toHaveBeenCalled());
    expect(posted.some((m) => m.action === "unlock")).toBe(false); // the server has not answered yet
    await act(async () => release({ status: "submitted", submitted_at: "t" }));
    await waitFor(() => expect(posted.some((m) => m.action === "unlock")).toBe(true));
    expect(await screen.findByText("Sample exam stopped")).toBeTruthy();
    expect(screen.queryByText(/^home/)).toBeNull(); // still locked: stay on the done page
    act(() => send({ type: "unlocked" }));
    expect(await screen.findByText("home stopped")).toBeTruthy();
  });

  it("is disabled while the lock is being requested, and no lock follows a stop", async () => {
    const posted: Array<Record<string, unknown>> = [];
    const postMessage = vi.fn((msg: Record<string, unknown>) => posted.push(msg)); // never answers preflight
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    api.getAttempt.mockResolvedValue(view());
    api.trialKey.mockResolvedValue({ key: "00".repeat(32) });
    api.trialStop.mockResolvedValue({ status: "submitted", submitted_at: "t" });
    online();
    at("/student/exam/a_t");
    const stop = (await screen.findByRole("button", { name: "Stop practice" })) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    expect(posted.some((m) => m.action === "lock")).toBe(false);
  });

  it("offline: offers retry and the sample-only exit code made from the app's code", async () => {
    nativeHost();
    api.getAttempt.mockResolvedValue(view());
    const key = hex(Uint8Array.from({ length: 32 }, (_, i) => i));
    api.trialKey.mockResolvedValue({ key });
    api.trialStop.mockRejectedValue(new NetworkError());
    online();
    at("/student/exam/a_t");
    await screen.findByText("Which is prime?");
    await waitFor(() => expect(api.trialKey).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Stop practice" }));
    expect(await screen.findByTestId("trial-exit")).toBeTruthy();
    expect(screen.queryByText(/invigilator to end/i)).toBeNull();
    fireEvent.change(screen.getByLabelText(/Type that Mac code here/), { target: { value: "K7QM-3XPA" } });
    expect(screen.getByTestId("trial-exit-code").textContent).toBe("0671 0479");
    api.trialStop.mockResolvedValue({ status: "submitted", submitted_at: "t" });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Sample exam stopped")).toBeTruthy();
  });

  it("a refused lock (bad session) offers a new sample exam, not the start code", async () => {
    const postMessage = vi.fn((msg: Record<string, unknown>) => {
      if (msg.action === "preflight") setTimeout(() => send({ type: "preflight", problems: [], apps_to_close: [] }), 0);
      if (msg.action === "lock") setTimeout(() => send({ type: "lockFailed", reason: "bad_session" }), 0);
    });
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { proctora: { postMessage } } };
    api.getAttempt.mockResolvedValue(view());
    api.trialKey.mockResolvedValue({ key: "00".repeat(32) });
    api.startTrial.mockResolvedValue(view({ attempt_id: "a_new" }));
    online();
    at("/student/exam/a_t");
    expect(await screen.findByTestId("trial-ended")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(api.startTrial).toHaveBeenCalled());
  });

  it("finishing normally goes home too (no score page)", async () => {
    api.getAttempt.mockResolvedValue(view());
    api.trialKey.mockResolvedValue({ key: "00".repeat(32) });
    api.submit.mockResolvedValue({ status: "submitted", submitted_at: "t", receipt: "R" });
    online();
    at("/student/exam/a_t");
    fireEvent.click(await screen.findByRole("button", { name: "Review and submit" }));
    const submit = await screen.findAllByRole("button", { name: /submit/i });
    fireEvent.click(submit[submit.length - 1]!);
    const confirm = screen.queryAllByRole("button", { name: /submit/i });
    if (confirm.length && !api.submit.mock.calls.length) fireEvent.click(confirm[confirm.length - 1]!);
    expect(await screen.findByText("home finished")).toBeTruthy();
  });
});
