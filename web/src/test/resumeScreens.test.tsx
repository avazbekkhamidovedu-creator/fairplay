import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, studentApi: { listExams: vi.fn(), start: vi.fn() } };
});

import { studentApi } from "../api/client";
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
};

beforeEach(() => {
  api.listExams.mockResolvedValue([EXAM]);
});
afterEach(() => {
  vi.clearAllMocks();
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

describe("start screens always ask for the start code", () => {
  it("before-start rules and lock screen ask for the start code", async () => {
    renderBefore();
    expect(await screen.findByText("Before you start")).toBeInTheDocument();
    expect(screen.getByText(/Then type the start code/)).toBeInTheDocument();
    await toCodeScreen();
    expect(await screen.findByRole("heading", { name: "Enter the start code" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume exam" })).toBeNull();
  });

  it("check-in asks for the start code", async () => {
    renderCheckIn();
    expect(await screen.findByRole("button", { name: "Start exam" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Start code" })).toBeInTheDocument();
  });
});
