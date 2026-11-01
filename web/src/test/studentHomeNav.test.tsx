import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setToken } from "../api/client";
import { _resetFeaturesForTests } from "../lib/features";
import ExamsPage from "../pages/student/ExamsPage";
import StudentHome from "../pages/student/StudentHome";

const b64 = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

beforeEach(() => {
  localStorage.clear();
  setToken(`${b64("7:student:9999999999:0:abc12345")}.sig`, "student");
  _resetFeaturesForTests();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true, features: [] }), { status: 200 });
      if (url === "/api/me")
        return new Response(JSON.stringify({ username: "a", name: "Alice A", role: "student", netid: "aa1" }), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("student top navigation", () => {
  it("Home keeps the same top nav as Exams", async () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={["/student/exams"]}>
        <Routes>
          <Route path="/student/exams" element={<ExamsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("stu-topnav")).toBeTruthy();
    unmount();
    render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<StudentHome />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("stu-topnav")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Exams" })).toBeTruthy();
    expect(await screen.findByRole("heading", { name: /Hi Alice/ })).toBeTruthy();
  });
});
