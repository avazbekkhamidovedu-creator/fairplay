import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, studentApi: { listExams: vi.fn(), start: vi.fn() } };
});

import { studentApi } from "../api/client";
import { rememberResume } from "../lib/resumeState";
import BeforeStartPage from "../pages/student/BeforeStartPage";
import CheckInPage from "../pages/student/CheckInPage";

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
  access_code: true,
};

beforeEach(() => {
  api.listExams.mockResolvedValue([EXAM]);
});
afterEach(() => {
  vi.clearAllMocks();
  rememberResume(false, "");
});

function renderBefore() {
  render(
    <MemoryRouter initialEntries={["/student/exams/e_1/start"]}>
      <Routes>
        <Route path="/student/exams/:examId/start" element={<BeforeStartPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderCheckIn() {
  render(
    <MemoryRouter initialEntries={["/student/exams/e_1/check-in"]}>
      <Routes>
        <Route path="/student/exams/:examId/check-in" element={<CheckInPage />} />
        <Route path="/student/exams/:examId/start" element={<p>start page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function toCodeScreen() {
  fireEvent.click(await screen.findByRole("checkbox", { name: /ready/ }));
  fireEvent.click(screen.getByRole("button", { name: "Lock and continue" }));
}

describe("resume wording on the start screens", () => {
  it("before-start rules and lock screen keep Start wording without resume", async () => {
    renderBefore();
    expect(await screen.findByText("Before you start")).toBeInTheDocument();
    expect(screen.getByText(/Then start the exam\./)).toBeInTheDocument();
    await toCodeScreen();
    expect(await screen.findByRole("heading", { name: "Start the exam" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start exam" })).toBeInTheDocument();
  });

  it("before-start rules and lock screen say resume when the student is resuming", async () => {
    rememberResume(true, "Avazbek Khamidov");
    renderBefore();
    expect(await screen.findByText("Welcome back")).toBeInTheDocument();
    expect(screen.getByText(/Then resume the exam\./)).toBeInTheDocument();
    expect(screen.queryByText(/start the exam/i)).toBeNull();
    await toCodeScreen();
    expect(await screen.findByRole("heading", { name: "Resume the exam" })).toBeInTheDocument();
    expect(screen.getByText("Welcome back, Avazbek. Your answers are saved. Continue where you stopped.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume exam" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start exam" })).toBeNull();
  });

  it("check-in keeps Start exam without resume", async () => {
    renderCheckIn();
    expect(await screen.findByRole("button", { name: "Start exam" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Start" })).toBeInTheDocument();
  });

  it("check-in says Resume exam when the student is resuming", async () => {
    rememberResume(true, "Avazbek Khamidov");
    renderCheckIn();
    expect(await screen.findByRole("button", { name: "Resume exam" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText("Welcome back, Avazbek. Your answers are saved. Continue where you stopped.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start exam" })).toBeNull();
  });
});
