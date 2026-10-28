import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearToken, getToken, setToken } from "../api/client";
import type { StudentExamSummary } from "../api/types";
import { _resetFeaturesForTests } from "../lib/features";
import StudentHome from "../pages/student/StudentHome";

const b64 = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const adminToken = () => `${b64("9:student:9999999999:0:abc12345:admin_test")}.sig`;

const exam = (id: string, status: StudentExamSummary["status"], title: string): StudentExamSummary => ({
  id,
  title,
  duration_min: 60,
  question_count: 5,
  status,
  course: "CS 101",
  room: "",
  scheduled_at: "2026-10-28T10:00:00.000Z",
  attempt: null,
  preview: true,
});

beforeEach(() => {
  _resetFeaturesForTests();
  setToken("student", adminToken());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true, features: [] }), { status: 200 });
      if (url === "/api/me") return new Response(JSON.stringify({ username: "admin-tester", name: "Admin Tester", role: "student" }), { status: 200 });
      if (url === "/api/student/exams")
        return new Response(
          JSON.stringify([exam("e1", "live", "Live midterm"), exam("e2", "draft", "Draft final"), exam("e3", "closed", "Old quiz")]),
          { status: 200 },
        );
      return new Response("[]", { status: 200 });
    }),
  );
});

afterEach(() => {
  clearToken("student");
  vi.unstubAllGlobals();
});

describe("admin preview dashboard", () => {
  it("lists every group with Preview only, no Start button and the Admin Tester pill", async () => {
    render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<StudentHome />} />
          <Route path="/student/login" element={<p>Code screen</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Live midterm", { exact: false })).toBeInTheDocument();
    for (const g of ["Open now", "Upcoming", "Closed"]) expect(screen.getByRole("region", { name: g })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Closed" })).getByText("Old quiz", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByTestId("preview-only")).toHaveLength(3);
    expect(screen.getByText("Admin Tester")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /start|check in/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /start|continue exam/i })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Sign out now" }));
    await waitFor(() => expect(screen.getByText("Code screen")).toBeInTheDocument());
    expect(getToken("student")).toBeNull();
  });
});
