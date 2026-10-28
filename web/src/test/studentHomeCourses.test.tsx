// 2026-10-24: sticky student top bar, course cards, "Join another class", long lists.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StudentClass, StudentExamSummary } from "../api/types";
import { setToken } from "../api/client";
import { COURSES_SHOWN, examInCourse, sortCourses } from "../components/student/Courses";
import ExamsPage, { PAST_SHOWN } from "../pages/student/ExamsPage";
import StudentHome, { HOME_OPEN_SHOWN } from "../pages/student/StudentHome";

type Reply = { status: number; body?: unknown };
let routes: Record<string, Reply>;
let posted: { url: string; body: unknown }[];

beforeEach(() => {
  routes = {};
  posted = [];
  setToken("student", "TOK");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method !== "GET") posted.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true, features: ["legacy_student_ui"] }), { status: 200 });
      if (url === "/api/me") return new Response(JSON.stringify({ username: "bob", name: "Bob Li", role: "student" }), { status: 200 });
      const r = routes[`${method} ${url}`] ?? { status: 404, body: { detail: "Not found" } };
      return new Response(r.body === undefined ? "" : JSON.stringify(r.body), { status: r.status });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

function exam(i: number, over: Partial<StudentExamSummary> = {}): StudentExamSummary {
  return {
    id: `e${i}`,
    title: `Exam ${i}`,
    duration_min: 60,
    question_count: 10,
    status: "live",
    course: "CS 101",
    room: "",
    scheduled_at: null,
    attempt: null,
    ...over,
  };
}

function submitted(i: number, course = "CS 101"): StudentExamSummary {
  return exam(i, {
    status: "closed",
    course,
    attempt: {
      attempt_id: `a${i}`,
      status: "submitted",
      submitted_at: `2026-10-${String((i % 28) + 1).padStart(2, "0")}T10:00:00Z`,
      receipt: null,
      released: false,
      percent: null,
    },
  });
}

const DKU: StudentClass = {
  id: "dkuoj:31",
  name: "CS 101 Section A",
  course: "CS 101 Programming",
  source: "dkuoj",
  teacher: "Shuai Dong",
  upcoming_exams: 2,
  next_exam: { title: "Midterm 1", starts_at: "2026-10-30T06:00:00Z", status: "draft" },
  can_leave: false,
};
const NATIVE: StudentClass = { id: 12, name: "Calculus I", term: "Fall 2026", teacher: "Ann Wu", joined_at: "2026-09-01T00:00:00Z" };

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/student"]}>
      <Routes>
        <Route path="/student" element={<StudentHome />} />
        <Route path="/student/exams" element={<p>exams page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("sticky student top bar", () => {
  it("the layout header is sticky and marks itself once the page scrolls", async () => {
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: [] };
    renderHome();
    const bar = await screen.findByTestId("stu-topnav");
    expect(bar.tagName).toBe("HEADER");
    expect(bar).toHaveClass("stu-topnav", "stu-topnav-sticky");
    expect(bar).not.toHaveClass("scrolled");
    expect(within(bar).getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    Object.defineProperty(window, "scrollY", { value: 300, configurable: true });
    window.dispatchEvent(new Event("scroll"));
    await waitFor(() => expect(bar).toHaveClass("scrolled"));
    Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
    window.dispatchEvent(new Event("scroll"));
    await waitFor(() => expect(bar).not.toHaveClass("scrolled"));
    // page content sits in its own area after the bar
    expect(bar.nextElementSibling).toHaveClass("stu-content");
  });
});

describe("course cards", () => {
  it("shows DKU OJ and FairPlay courses with badge, upcoming count and next exam", async () => {
    routes["GET /api/student/exams"] = { status: 200, body: [exam(1, { course: "CS 101" })] };
    routes["GET /api/student/classes"] = { status: 200, body: [NATIVE, DKU] };
    renderHome();
    const cards = await screen.findAllByTestId("course-card");
    expect(cards).toHaveLength(2);
    // the course with an open exam comes first
    const dku = cards[0]!;
    expect(dku).toHaveTextContent("CS 101 Programming");
    expect(dku).toHaveTextContent("CS 101 Section A · Shuai Dong");
    expect(within(dku).getByText("DKU OJ")).toBeInTheDocument();
    expect(within(dku).getByTestId("course-upcoming")).toHaveTextContent("2 upcoming exams");
    expect(dku).toHaveTextContent(/Next:\s*Midterm 1/);
    // 2026-10-24: a card is a link to the course's own page
    expect(dku).toHaveAttribute("href", "/student/courses/dkuoj%3A31");

    const native = cards[1]!;
    expect(native).toHaveTextContent("Calculus I");
    expect(native).toHaveTextContent("Fall 2026 · Ann Wu");
    expect(within(native).getByText("FairPlay")).toBeInTheDocument();
    expect(within(native).getByTestId("course-upcoming")).toHaveTextContent("No upcoming exams");
    expect(native).toHaveTextContent("No exam planned yet");
    // long titles keep their full text in a tooltip
    expect(within(dku).getByTitle("CS 101 Programming")).toBeInTheDocument();
  });

  it("clicking a card opens the course page (no panel under the cards any more)", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/exams"] = { status: 200, body: [exam(1, { title: "Quiz 3", course: "CS 101" })] };
    routes["GET /api/student/classes"] = { status: 200, body: [DKU, NATIVE] };
    render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<StudentHome />} />
          <Route path="/student/courses/:classId" element={<p>course page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    const [dku] = await screen.findAllByTestId("course-card");
    expect(screen.queryByTestId("course-exams")).not.toBeInTheDocument();
    await user.click(dku!);
    expect(await screen.findByText("course page")).toBeInTheDocument();
  });

  it("empty state when the student has no courses", async () => {
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: [] };
    renderHome();
    expect(await screen.findByTestId("courses-empty")).toHaveTextContent("No courses yet");
    expect(screen.getByRole("heading", { name: "Join a class" })).toBeInTheDocument();
  });

  it("copes with the old response shape (no course, source or counts)", async () => {
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: [{ id: 4, name: "Physics", term: "", teacher: "Tom" }] };
    renderHome();
    const [card] = await screen.findAllByTestId("course-card");
    expect(card).toHaveTextContent("Physics");
    expect(card).toHaveTextContent("FairPlay");
    expect(card).toHaveTextContent("No upcoming exams");
  });
});

describe("join another class", () => {
  it("explains DKU OJ courses, joins with a code and shows success", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: [DKU] };
    routes["POST /api/student/classes/join"] = { status: 200, body: { id: 12, name: "Calculus I", teacher: "Ann Wu" } };
    renderHome();
    const box = await screen.findByTestId("join-class");
    expect(within(box).getByRole("heading", { name: "Join another class" })).toBeInTheDocument();
    expect(box).toHaveTextContent("Your DKU OJ courses appear here by themselves. Use a class code only if your teacher gave you one.");
    const join = within(box).getByRole("button", { name: "Join" });
    expect(join).toBeDisabled();
    await user.type(within(box).getByLabelText("Class code"), "k7qm-3xpa");
    expect(within(box).getByLabelText("Class code")).toHaveValue("K7QM-3XPA");
    // the course list is fetched again after joining
    routes["GET /api/student/classes"] = { status: 200, body: [DKU, NATIVE] };
    await user.click(join);
    expect(await within(box).findByRole("status")).toHaveTextContent("You joined Calculus I (Ann Wu).");
    expect(posted).toContainEqual({ url: "/api/student/classes/join", body: { code: "K7QM-3XPA" } });
    await waitFor(() => expect(screen.getAllByTestId("course-card")).toHaveLength(2));
  });

  it("shows the server's error inline", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: [] };
    routes["POST /api/student/classes/join"] = { status: 404, body: { detail: "No class has this code. Check it with your teacher." } };
    renderHome();
    const box = await screen.findByTestId("join-class");
    await user.type(within(box).getByLabelText("Class code"), "AAAABBBB");
    await user.click(within(box).getByRole("button", { name: "Join" }));
    expect(await within(box).findByRole("alert")).toHaveTextContent("No class has this code. Check it with your teacher.");
  });
});

describe("many courses and exams", () => {
  const many: StudentClass[] = Array.from({ length: 40 }, (_, i) => ({
    id: `dkuoj:${i}`,
    name: `Section ${i}`,
    course: `COURSE ${String(i).padStart(2, "0")} ${"Very long course name ".repeat(i % 3 ? 1 : 6)}`.trim(),
    source: i % 2 ? "dkuoj" : "fairplay",
    teacher: "T",
    upcoming_exams: i % 4,
    next_exam: null,
  }));

  it("40 courses: the first ones, then Show all", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/classes"] = { status: 200, body: many };
    renderHome();
    expect(await screen.findAllByTestId("course-card")).toHaveLength(COURSES_SHOWN);
    await user.click(screen.getByRole("button", { name: "Show all 40 courses" }));
    expect(screen.getAllByTestId("course-card")).toHaveLength(40);
    await user.click(screen.getByRole("button", { name: "Show fewer courses" }));
    expect(screen.getAllByTestId("course-card")).toHaveLength(COURSES_SHOWN);
  });

  it("many open exams: compact cards on the home page and a link to the rest", async () => {
    routes["GET /api/student/exams"] = { status: 200, body: Array.from({ length: 12 }, (_, i) => exam(i)) };
    routes["GET /api/student/classes"] = { status: 200, body: [] };
    renderHome();
    expect(await screen.findByText("Open now · 12")).toBeInTheDocument();
    expect(screen.getByTestId("tests-active")).toBeInTheDocument();
    expect(screen.getAllByTestId("exam-card")).toHaveLength(HOME_OPEN_SHOWN);
    expect(screen.getByRole("link", { name: "See all 12 open exams →" })).toHaveAttribute("href", "/student/exams");
  });

  it("Exams page: 60 past exams show the first ones, then Show all", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/exams"] = { status: 200, body: Array.from({ length: 60 }, (_, i) => submitted(i)) };
    render(
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("tab", { name: "Past · 60" }));
    const panel = screen.getByRole("tabpanel");
    expect(panel.querySelectorAll("[data-testid=grade-card]")).toHaveLength(PAST_SHOWN);
    await user.click(within(panel).getByRole("button", { name: "Show all 60 results" }));
    expect(panel.querySelectorAll("[data-testid=grade-card]")).toHaveLength(60);
  });
});

describe("course helpers", () => {
  it("matches exams to a course by course text or class name", () => {
    expect(examInCourse(exam(1, { course: "CS 101" }), DKU)).toBe(true);
    expect(examInCourse(exam(1, { course: "cs 101 programming" }), DKU)).toBe(true);
    expect(examInCourse(exam(1, { course: "CS 1010" }), DKU)).toBe(false);
    expect(examInCourse(exam(1, { course: "Calculus I" }), NATIVE)).toBe(true);
    expect(examInCourse(exam(1, { course: "" }), NATIVE)).toBe(false);
  });

  it("sorts courses: something open first, then the soonest next exam, then name", () => {
    const a: StudentClass = { id: 1, name: "B course" };
    const b: StudentClass = { id: 2, name: "A course" };
    const c: StudentClass = { id: 3, name: "Z", next_exam: { title: "x", starts_at: "2026-11-01T00:00:00Z", status: "draft" } };
    const d: StudentClass = { id: 4, name: "Y", next_exam: { title: "x", starts_at: null, status: "live" } };
    expect(sortCourses([a, b, c, d], []).map((k) => k.id)).toEqual([4, 3, 2, 1]);
  });
});
