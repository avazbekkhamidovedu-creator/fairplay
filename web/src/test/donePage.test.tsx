import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    auth: { login: vi.fn(), me: vi.fn().mockResolvedValue({ username: "alice", name: "Alice Chen", role: "student" }) },
    studentApi: { listExams: vi.fn(), getAttempt: vi.fn() },
  };
});

import { studentApi } from "../api/client";
import DonePage from "../pages/student/DonePage";

const api = studentApi as unknown as Record<"listExams" | "getAttempt", ReturnType<typeof vi.fn>>;

function renderDone(state: Record<string, unknown>) {
  return render(
    <MemoryRouter initialEntries={[{ pathname: "/student/done/a_1", state }]}>
      <Routes>
        <Route path="/student/done/:attemptId" element={<DonePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("done page after hand-in", () => {
  beforeEach(() => {
    api.listExams.mockResolvedValue([]);
    api.getAttempt.mockResolvedValue({ title: "Midterm" });
  });

  it("shows the Canvas wording and no receipt code", async () => {
    renderDone({ receipt: "K7Q2-M9XD", submitted_at: "2026-10-24T09:00:00Z", title: "Midterm", lms: true });
    expect(await screen.findByRole("heading", { name: "Congratulations, your exam is submitted." })).toBeInTheDocument();
    expect(screen.getByTestId("done-grade-note")).toHaveTextContent(
      "You will receive your grade on Canvas. If you have a question about your exam, please ask your professor.",
    );
    expect(screen.queryByText("K7Q2-M9XD")).toBeNull();
    expect(screen.queryByTestId("receipt")).toBeNull();
    expect(screen.getByRole("link", { name: "Back to home" })).toBeInTheDocument();
  });

  it("uses neutral wording for other exams", async () => {
    renderDone({ receipt: "K7Q2-M9XD", submitted_at: "", title: "Quiz", lms: false });
    expect(await screen.findByTestId("done-grade-note")).toHaveTextContent("Your professor will share your grade.");
    expect(screen.queryByText(/Canvas/)).toBeNull();
  });

  it("looks up the exam type when the page is opened without it", async () => {
    api.getAttempt.mockResolvedValue({ title: "OJ", source: "dkuoj" });
    renderDone({ receipt: "R", submitted_at: "", title: "OJ" });
    expect(await screen.findByText(/grade on Canvas/)).toBeInTheDocument();
  });
});
