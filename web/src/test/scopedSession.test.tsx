import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getToken, setToken, tokenScope } from "../api/client";
import type { StudentExamSummary } from "../api/types";
import { HandInSignOut } from "../components/student/HandInSignOut";
import { _resetFeaturesForTests } from "../lib/features";
import { hasPendingDeviceCheck, keepDeviceCheck } from "../lib/pendingDeviceCheck";
import ExamsPage from "../pages/student/ExamsPage";
import StudentHome from "../pages/student/StudentHome";

const b64 = (s: string) => btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const scopedToken = (exam = "e_1") => `${b64(`7:student:9999999999:0:abc12345:${exam}`)}.sig`;
const plainToken = () => `${b64("7:student:9999999999:0:abc12345")}.sig`;

let exams: StudentExamSummary[];
let health: Record<string, unknown>;
let me: Record<string, unknown>;
let status401: boolean;

const exam = (over: Partial<StudentExamSummary> = {}): StudentExamSummary => ({
  id: "e_1",
  title: "Demo exam: access codes",
  duration_min: 60,
  question_count: null,
  status: "draft",
  course: "Demo course",
  room: "",
  scheduled_at: null,
  access_code: true,
  attempt: null,
  ...over,
});

beforeEach(() => {
  exams = [];
  health = { ok: true, features: [], student_grades: false };
  me = { username: "oj:ob996", name: "Olivia Brown", role: "student", netid: "ob996" };
  status401 = false;
  _resetFeaturesForTests();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/health") return new Response(JSON.stringify(health), { status: 200 });
      if (status401) return new Response(JSON.stringify({ detail: "Signed out." }), { status: 401 });
      if (url === "/api/me") return new Response(JSON.stringify(me), { status: 200 });
      if (url === "/api/student/exams") return new Response(JSON.stringify(exams), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    }),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  keepDeviceCheck(null);
});

const home = (el = <StudentHome />) =>
  render(
    <MemoryRouter initialEntries={["/student"]}>
      <Routes>
        <Route path="/student" element={el} />
        <Route path="/student/exams/:id/start" element={<p>Before you start</p>} />
        <Route path="/student/login" element={<p>Code screen</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("dashboard", () => {
  it("token scope is read from the token", () => {
    expect(tokenScope(scopedToken("e_9"))).toBe("e_9");
    expect(tokenScope(plainToken())).toBeNull();
    expect(tokenScope(null)).toBeNull();
  });

  it("waiting: countdown ticks, button is disabled, then the exam opens", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    setToken("student", scopedToken());
    exams = [exam({ scheduled_at: new Date(Date.now() + 5 * 60_000).toISOString() })];
    home();
    expect(await screen.findByText("Your exam opens in a few minutes.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Hi Olivia" })).toBeInTheDocument();
    expect(await screen.findByText("Olivia Brown · ob996")).toBeInTheDocument();
    expect(screen.getByText("Opens in 05:00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Waiting for the exam to open" })).toBeDisabled();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByText("Opens in 04:59")).toBeInTheDocument();
    exams = [exam({ status: "live", question_count: 6, scheduled_at: new Date(Date.now()).toISOString() })];
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(await screen.findByText("Open now")).toBeInTheDocument();
    expect(screen.getByText("Your exam is open now.")).toBeInTheDocument();
    expect(screen.getByText("6 questions")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start exam" })).toHaveAttribute("href", "/student/exams/e_1/start");
  });

  it("shows the sign-out line, and no courses, join box or grades", async () => {
    setToken("student", scopedToken());
    exams = [exam({ status: "live", scheduled_at: new Date(Date.now() - 60_000).toISOString() })];
    home();
    await screen.findByText("Open now");
    expect(
      screen.getByText(/After you hand in, you are signed out after 5 minutes and asked for the next exam's code\./),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Your courses/)).toBeNull();
    expect(screen.queryByText(/Join another class/i)).toBeNull();
    expect(screen.queryByText(/Grades/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Sign out now" }));
    expect(await screen.findByText("Code screen")).toBeInTheDocument();
    expect(getToken("student")).toBeNull();
  });

  it("the earlier home page comes back only with legacy_student_ui", async () => {
    setToken("student", plainToken());
    health = { ok: true, features: ["legacy_student_ui"] };
    home();
    expect(await screen.findByText("Your courses")).toBeInTheDocument();
    expect(screen.queryByTestId("newest-exam")).toBeNull();
  });
});

describe("grades switch", () => {
  it("off: no Past tab and no Grades link", async () => {
    setToken("student", plainToken());
    health = { ok: true, features: [], student_grades: false };
    render(
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>,
    );
    await screen.findByText("No open exams");
    expect(screen.getByRole("tab", { name: /Upcoming/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Past/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Grades" })).toBeNull();
  });

  it("on: today's tabs and link", async () => {
    setToken("student", plainToken());
    health = { ok: true, features: [], student_grades: true };
    render(
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("tab", { name: /Past/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grades" })).toBeInTheDocument();
  });
});

describe("sign-out after the hand-in", () => {
  function counting() {
    return render(
      <MemoryRouter initialEntries={["/student/done/a_1"]}>
        <Routes>
          <Route path="/student/done/:aid" element={<HandInSignOut />} />
          <Route path="/student/login" element={<p>Code screen</p>} />
        </Routes>
      </MemoryRouter>,
    );
  }
  const seedLocal = () => {
    localStorage.setItem("fairplay:me:student:7", '{"name":"Olivia","netId":"ob996"}');
    localStorage.setItem("fairplay:pkg:e_1", "{}");
    localStorage.setItem("fairplay:pkgkey:e_1", "k");
    localStorage.setItem("proctora:token:teacher", "T");
    keepDeviceCheck({ problems: [], apps_to_close: [], checks: [], platform: "mac", app_version: "1", os_version: "1" });
  };

  it("counts down 5:00 and at zero clears the token and every local student copy", async () => {
    vi.useFakeTimers();
    setToken("student", scopedToken());
    seedLocal();
    counting();
    expect(screen.getByText(/You will be signed out in/)).toHaveTextContent("You will be signed out in 5:00");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    expect(screen.getByText(/You will be signed out in/)).toHaveTextContent("You will be signed out in 3:59");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(240_000);
    });
    expect(screen.getByText("Code screen")).toBeInTheDocument();
    expect(getToken("student")).toBeNull();
    expect(localStorage.getItem("fairplay:me:student:7")).toBeNull();
    expect(localStorage.getItem("fairplay:pkg:e_1")).toBeNull();
    expect(localStorage.getItem("fairplay:pkgkey:e_1")).toBeNull();
    expect(hasPendingDeviceCheck()).toBe(false);
    expect(localStorage.getItem("proctora:token:teacher")).toBe("T");
  });

  it("Sign out now does the same at once", async () => {
    setToken("student", scopedToken());
    seedLocal();
    counting();
    await userEvent.click(screen.getByRole("button", { name: "Sign out now" }));
    expect(await screen.findByText("Code screen")).toBeInTheDocument();
    expect(getToken("student")).toBeNull();
    expect(localStorage.getItem("fairplay:pkg:e_1")).toBeNull();
    expect(hasPendingDeviceCheck()).toBe(false);
  });

  it("a normal sign-in gets no countdown", () => {
    setToken("student", plainToken());
    counting();
    expect(screen.queryByTestId("signout-timer")).toBeNull();
  });

  it("a 401 on a scoped token signs out at once and clears the local copies", async () => {
    setToken("student", scopedToken());
    seedLocal();
    const { studentApi } = await import("../api/client");
    status401 = true;
    await expect(studentApi.listExams()).rejects.toBeTruthy();
    expect(getToken("student")).toBeNull();
    expect(localStorage.getItem("fairplay:pkg:e_1")).toBeNull();
    expect(localStorage.getItem("fairplay:me:student:7")).toBeNull();
    expect(hasPendingDeviceCheck()).toBe(false);
  });
});

