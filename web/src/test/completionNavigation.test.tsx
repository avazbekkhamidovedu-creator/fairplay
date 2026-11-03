import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    auth: { me: vi.fn().mockResolvedValue({ username: "alice", name: "Alice Chen", role: "student" }) },
    studentApi: { listExams: vi.fn(), getAttempt: vi.fn() },
  };
});

import { getToken, setToken, studentApi } from "../api/client";
import { StudentLayout } from "../components/student/StudentLayout";
import * as bridge from "../lib/bridge";
import { _resetFeaturesForTests } from "../lib/features";
import DonePage from "../pages/student/DonePage";

const api = studentApi as unknown as Record<"listExams" | "getAttempt", ReturnType<typeof vi.fn>>;
const receipt = { receipt: "R", submitted_at: "2026-11-02T09:00:00Z", title: "Midterm", lms: true };
const send = (detail: unknown) => act(() => {
  window.dispatchEvent(new CustomEvent("proctora-native", { detail }));
});

function native(platform: "mac" | "windows", locked: boolean) {
  if (platform === "mac") window.webkit = { messageHandlers: { proctora: { postMessage: vi.fn() } } };
  else window.chrome = { webview: { postMessage: vi.fn(), addEventListener: vi.fn() } };
  bridge.hello();
  send({ type: "state", platform, locked, trusted: true, version: "1" });
}

function renderDone(state: Record<string, unknown> | null = receipt) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/student/done/a_1", state }]}>
      <Routes>
        <Route path="/student/done/:attemptId" element={<DonePage />} />
        <Route path="/student" element={<StudentLayout><h1>Student dashboard</h1></StudentLayout>} />
        <Route path="/student/login" element={<p>Code screen</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function expectCompletionHeader() {
  const header = within(screen.getByTestId("stu-topnav"));
  expect(header.getByLabelText("FairPlay")).toBeInTheDocument();
  expect(header.queryByRole("navigation")).toBeNull();
  expect(header.queryByRole("link")).toBeNull();
  expect(header.queryByRole("button", { name: "Sign out" })).toBeNull();
}

beforeEach(() => {
  bridge._resetBridgeForTests();
  _resetFeaturesForTests();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    features: ["practice"], student_grades: true,
  }))));
  api.listExams.mockResolvedValue([]);
  api.getAttempt.mockResolvedValue({ title: "Midterm", external_kind: "canvas" });
});

afterEach(() => {
  delete window.webkit;
  delete window.chrome;
  bridge._resetBridgeForTests();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe.each(["mac", "windows"] as const)("%s completion navigation", (platform) => {
  it.each([false, true])("has no dashboard links before or after unlock (initially locked: %s)", async (locked) => {
    native(platform, locked);
    renderDone();
    expect(await screen.findByRole("heading", { name: "Congratulations, your exam is submitted." })).toBeInTheDocument();
    expectCompletionHeader();
    send({ type: "unlocked" });
    expectCompletionHeader();
    // A later state refresh or repeated native unlock must not restore dashboard navigation.
    send({ type: "state", platform, locked: false, trusted: true, version: "1" });
    send({ type: "unlocked" });
    expectCompletionHeader();
    expect(screen.getByTestId("done-grade-note")).toHaveTextContent("You will receive your grade on Canvas.");
    expect(screen.getByRole("link", { name: "Back to home" })).toBeInTheDocument();
  });

  it("keeps the completion header after a direct load without route state", async () => {
    native(platform, false);
    api.listExams.mockResolvedValue([{ title: "Reloaded exam", attempt: {
      attempt_id: "a_1", receipt: "R", submitted_at: receipt.submitted_at,
    } }]);
    renderDone(null);
    expect(await screen.findByText("Reloaded exam")).toBeInTheDocument();
    expectCompletionHeader();
  });
});

it("restores normal dashboard navigation only after leaving the completion page", async () => {
  renderDone();
  expectCompletionHeader();
  fireEvent.click(screen.getByRole("link", { name: "Back to home" }));
  expect(await screen.findByRole("heading", { name: "Student dashboard" })).toBeInTheDocument();
  const nav = within(screen.getByRole("navigation", { name: "Main" }));
  expect(nav.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/student");
  expect(nav.getByRole("link", { name: "Exams" })).toHaveAttribute("href", "/student/exams");
  expect(nav.getByRole("link", { name: "Grades" })).toBeInTheDocument();
  expect(await nav.findByRole("link", { name: "Practice" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
});

it("preserves the code-session countdown and Sign out now action", async () => {
  const payload = btoa("7:student:9999999999:0:abc12345:e_1").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  setToken("student", `${payload}.sig`);
  renderDone();
  expect(await screen.findByTestId("signout-timer")).toHaveTextContent("5:00");
  expectCompletionHeader();
  fireEvent.click(screen.getByRole("button", { name: "Sign out now" }));
  expect(await screen.findByText("Code screen")).toBeInTheDocument();
  expect(getToken("student")).toBeNull();
});

it("keeps trial completion navigation hidden until its automatic return home", async () => {
  native("mac", true);
  renderDone({ ...receipt, trial: "finished" });
  expect(await screen.findByRole("heading", { name: "Sample questions finished" })).toBeInTheDocument();
  expectCompletionHeader();
  send({ type: "unlocked" });
  expect(await screen.findByRole("heading", { name: "Student dashboard" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Exams" })).toBeInTheDocument();
});
