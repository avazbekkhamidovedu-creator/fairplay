// 2026-10-24: a course's own page (Active / Past), the home page's "Your tests" tabs,
// More -> Text size − / +, and the centred start-code boxes.
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StudentClass, StudentExamSummary, StudentGradeSummary } from "../api/types";
import { setToken } from "../api/client";
import { TextSizeRow } from "../components/student/LockedBar";
import { StartCodeForm } from "../components/student/StartCodeForm";
import { scheduledExams } from "../components/student/ExamTabs";
import CoursePage, { instructorLabel } from "../pages/student/CoursePage";
import StudentHome from "../pages/student/StudentHome";

type Reply = { status: number; body?: unknown };
let routes: Record<string, Reply>;

beforeEach(() => {
  routes = {};
  setToken("student", "TOK");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/health") return new Response(JSON.stringify({ ok: true, features: [] }), { status: 200 });
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

function exam(id: string, over: Partial<StudentExamSummary> = {}): StudentExamSummary {
  return {
    id,
    title: `Exam ${id}`,
    duration_min: 45,
    question_count: 12,
    status: "live",
    course: "CS 101",
    room: "",
    scheduled_at: null,
    attempt: null,
    class_ids: ["dkuoj:31"],
    ...over,
  };
}

function handedIn(
  id: string,
  over: { released?: boolean; percent?: number | null; review?: "nothing" | "answers" | "answers_and_results"; source?: string; class_ids?: string[] } = {},
): StudentExamSummary {
  return exam(id, {
    status: "closed",
    source: over.source ?? "fairplay",
    class_ids: over.class_ids ?? ["dkuoj:31"],
    attempt: {
      attempt_id: `a-${id}`,
      status: "submitted",
      submitted_at: "2026-10-12T10:00:00Z",
      receipt: null,
      released: !!over.released,
      percent: over.percent ?? null,
      ...(over.review ? { review: over.review } : {}),
    },
  });
}

const DKU: StudentClass = {
  id: "dkuoj:31",
  name: "CS 101 Section A",
  course: "CS 101 Programming",
  source: "dkuoj",
  teacher: "Shuai Dong",
  upcoming_exams: 3,
  next_exam: { title: "Midterm 1", starts_at: "2026-10-30T06:00:00Z", status: "draft", duration_min: 90, question_count: 20 },
  can_leave: false,
};
const NATIVE: StudentClass = { id: 12, name: "Calculus I", term: "Fall 2026", teacher: "Ann Wu", source: "fairplay", upcoming_exams: 0, next_exam: null };

function Where() {
  const loc = useLocation();
  return <span data-testid="where">{loc.pathname + loc.search}</span>;
}

function renderCourse(path = "/student/courses/dkuoj%3A31") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/student/courses/:classId" element={<CoursePage />} />
        <Route path="/student" element={<p>home page</p>} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );
}

describe("course page", () => {
  it("shows the course, section, instructor and source, then the Active tab", async () => {
    routes["GET /api/student/classes"] = { status: 200, body: [DKU, NATIVE] };
    routes["GET /api/student/exams"] = {
      status: 200,
      body: [exam("q3", { title: "Quiz 3" }), exam("other", { title: "Calc quiz", class_ids: ["12"] })],
    };
    routes["GET /api/student/grades"] = { status: 200, body: [] };
    renderCourse();
    const head = await screen.findByTestId("course-head");
    expect(within(head).getByRole("heading", { level: 1, name: "CS 101 Programming" })).toBeInTheDocument();
    expect(head).toHaveTextContent("SectionCS 101 Section A");
    expect(head).toHaveTextContent("InstructorShuai Dong");
    expect(within(head).getByText("DKU OJ")).toBeInTheDocument();
    expect(screen.getByTestId("course-back")).toHaveAttribute("href", "/student");
    expect(screen.getByTestId("course-back")).toHaveTextContent("Home");

    const tabs = screen.getByTestId("exam-tabs");
    expect(within(tabs).getByRole("tab", { name: /Active/ })).toHaveAttribute("aria-selected", "true");
    const panel = await screen.findByTestId("tests-active");
    // live now: the normal check-in action
    const live = await within(panel).findByTestId("exam-card");
    expect(within(live).getByRole("heading", { name: "Quiz 3" })).toBeInTheDocument();
    expect(live).toHaveTextContent("45 min · 12 questions");
    expect(within(live).getByRole("link", { name: "Check in" })).toHaveAttribute("href", "/student/exams/q3/checkin");
    // coming up: from the course's next exam, with its length
    const sched = within(panel).getByTestId("scheduled-card");
    expect(sched).toHaveTextContent("Midterm 1");
    expect(sched).toHaveTextContent("90 min · 20 questions");
    expect(within(sched).getByTestId("scheduled-status")).toHaveTextContent("Not open yet");
    // the server counts one more exam than it lists
    expect(panel).toHaveTextContent("1 more exam planned");
    // another course's exam is not here
    expect(within(panel).queryByText(/Calc quiz/)).toBeNull();
  });

  it("friendly empty states on both tabs", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/classes"] = { status: 200, body: [NATIVE] };
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/grades"] = { status: 200, body: [] };
    renderCourse("/student/courses/12");
    expect(await screen.findByTestId("tests-empty")).toHaveTextContent(
      "No upcoming exams in this courseThey appear here when your teacher schedules them.",
    );
    expect(screen.getByTestId("course-head")).toHaveTextContent("FairPlay");
    expect(screen.getByTestId("course-head")).toHaveTextContent("TermFall 2026");
    await user.click(screen.getByRole("tab", { name: /Past/ }));
    expect(screen.getByTestId("tests-empty")).toHaveTextContent("No finished exams yet");
  });

  it("Past: grade when released and the review shows it, else Handed in / Being graded", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/classes"] = { status: 200, body: [DKU] };
    routes["GET /api/student/exams"] = {
      status: 200,
      body: [
        handedIn("rel", { released: true, percent: 80 }),
        handedIn("wait", {}),
        handedIn("ans", { source: "dkuoj", review: "answers" }),
        handedIn("none", { source: "dkuoj", review: "nothing" }),
      ],
    };
    const g: StudentGradeSummary[] = [
      { exam_id: "rel", title: "Exam rel", course: "CS 101", submitted_at: "2026-10-12T10:00:00Z", released: true, score: 8, total_points: 10, percent: 80 },
    ];
    routes["GET /api/student/grades"] = { status: 200, body: g };
    renderCourse();
    await screen.findByTestId("tests-active");
    await user.click(screen.getByRole("tab", { name: /Past/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("?tab=past");
    const panel = screen.getByTestId("tests-past");
    const row = (id: string) => within(panel).getByText(`Exam ${id}`).closest(".stu-result-row") as HTMLElement;

    const rel = row("rel");
    expect(await within(rel).findByTestId("result-score")).toHaveTextContent("8 / 1080%");
    expect(rel).toHaveTextContent("Handed in Oct 12");
    expect(rel).toHaveAttribute("href", "/student/grades/rel");
    expect(rel).toHaveTextContent("See feedback");

    const wait = row("wait");
    expect(within(wait).getByTestId("result-status")).toHaveTextContent("Being graded");
    expect(wait.tagName).toBe("DIV");

    const ans = row("ans");
    expect(within(ans).queryByTestId("result-status")).toBeNull();
    expect(ans).toHaveTextContent("Handed in Oct 12");
    expect(ans).toHaveAttribute("href", "/student/grades/ans");
    expect(ans).toHaveTextContent("See your answers");
    expect(within(ans).queryByTestId("result-score")).toBeNull();

    const none = row("none");
    expect(within(none).queryByTestId("result-status")).toBeNull();
    expect(none).toHaveTextContent("Handed in Oct 12");
    expect(none.tagName).toBe("DIV");
    expect(none).not.toHaveTextContent("See your answers");
  });

  it("the tabs work with the arrow keys and ?tab=past opens Past", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/classes"] = { status: 200, body: [DKU] };
    routes["GET /api/student/exams"] = { status: 200, body: [handedIn("x")] };
    routes["GET /api/student/grades"] = { status: 200, body: [] };
    renderCourse("/student/courses/dkuoj%3A31?tab=past");
    const past = await screen.findByRole("tab", { name: /Past/ });
    expect(past).toHaveAttribute("aria-selected", "true");
    expect(past).toHaveAttribute("tabindex", "0");
    past.focus();
    await user.keyboard("{ArrowLeft}");
    const active = screen.getByRole("tab", { name: /Active/ });
    expect(active).toHaveAttribute("aria-selected", "true");
    expect(active).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", active.id);
  });

  it("a course the student is not in", async () => {
    routes["GET /api/student/classes"] = { status: 200, body: [NATIVE] };
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/grades"] = { status: 200, body: [] };
    renderCourse("/student/courses/dkuoj%3A99");
    expect(await screen.findByTestId("course-missing")).toHaveTextContent("Course not found");
    expect(screen.getByRole("link", { name: "Back to Home" })).toHaveAttribute("href", "/student");
  });

  it("names one or more instructors", () => {
    expect(instructorLabel("Shuai Dong")).toBe("Instructor");
    expect(instructorLabel("Shuai Dong, Ann Wu")).toBe("Instructors");
    expect(instructorLabel("Shuai Dong and Ann Wu")).toBe("Instructors");
  });
});

describe("home page: Your tests [Active | Past]", () => {
  function renderHome() {
    return render(
      <MemoryRouter initialEntries={["/student"]}>
        <Routes>
          <Route path="/student" element={<StudentHome />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("Active: open now across courses plus each course's next exam; Past: results with grades", async () => {
    const user = userEvent.setup();
    routes["GET /api/student/classes"] = { status: 200, body: [DKU, NATIVE] };
    routes["GET /api/student/exams"] = {
      status: 200,
      body: [exam("q3", { title: "Quiz 3" }), handedIn("rel", { released: true, percent: 75 })],
    };
    routes["GET /api/student/grades"] = {
      status: 200,
      body: [{ exam_id: "rel", title: "Exam rel", course: "CS 101", submitted_at: null, released: true, score: 15, total_points: 20, percent: 75 }],
    };
    renderHome();
    const tests = await screen.findByTestId("tests");
    expect(within(tests).getByRole("heading", { name: "Your tests" })).toBeInTheDocument();
    const active = await within(tests).findByTestId("tests-active");
    // one open exam: the big card with Start exam
    expect(await within(active).findByRole("button", { name: "Start exam" })).toBeInTheDocument();
    expect(within(active).getByText("Open now · 1")).toBeInTheDocument();
    expect(within(active).getByTestId("scheduled-card")).toHaveTextContent("CS 101 Programming · Midterm 1");
    expect(within(tests).getByRole("tab", { name: /Active/ })).toHaveTextContent("2");

    await user.click(within(tests).getByRole("tab", { name: /Past/ }));
    const past = within(tests).getByTestId("tests-past");
    expect(await within(past).findByTestId("result-score")).toHaveTextContent("15 / 2075%");
    expect(within(past).getByRole("link", { name: "All grades →" })).toHaveAttribute("href", "/student/grades");
    // the course cards stay above the tests
    const courses = screen.getByTestId("courses");
    expect(courses.compareDocumentPosition(tests) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("empty Active tab", async () => {
    routes["GET /api/student/classes"] = { status: 200, body: [NATIVE] };
    routes["GET /api/student/exams"] = { status: 200, body: [] };
    routes["GET /api/student/grades"] = { status: 200, body: [] };
    renderHome();
    expect(await screen.findByTestId("tests-empty")).toHaveTextContent("No open or upcoming exams");
  });

  it("scheduled exams across courses: soonest first, undated last, live and closed left out", () => {
    const k = (id: number, next: StudentClass["next_exam"]): StudentClass => ({ id, name: `C${id}`, next_exam: next });
    const list = scheduledExams([
      k(1, { title: "Later", starts_at: "2026-11-20T00:00:00Z", status: "draft" }),
      k(2, { title: "No date", starts_at: null, status: "draft" }),
      k(3, { title: "Soon", starts_at: "2026-11-01T00:00:00Z", status: "draft" }),
      k(4, { title: "Open", starts_at: null, status: "live" }),
      k(5, { title: "Done", starts_at: null, status: "closed" }),
      k(6, null),
    ]);
    expect(list.map((s) => s.title)).toEqual(["Soon", "Later", "No date"]);
  });
});

describe("More -> Text size − / +", () => {
  it("steps one size at a time and stops at the ends", () => {
    const onChange = vi.fn();
    const { rerender } = render(<TextSizeRow size={1} onChange={onChange} />);
    const group = screen.getByRole("group", { name: "Text size" });
    expect(group).toHaveTextContent("100%");
    fireEvent.click(screen.getByRole("menuitem", { name: "Larger text" }));
    expect(onChange).toHaveBeenLastCalledWith(1.15);
    fireEvent.click(screen.getByRole("menuitem", { name: "Smaller text" }));
    expect(onChange).toHaveBeenLastCalledWith(0.9);
    // 100 %: the % does nothing
    fireEvent.click(screen.getByRole("menuitem", { name: "Text size 100%" }));
    expect(onChange).toHaveBeenCalledTimes(2);

    onChange.mockClear();
    rerender(<TextSizeRow size={1.5} onChange={onChange} />);
    const larger = screen.getByRole("menuitem", { name: "Larger text" });
    expect(larger).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: "Smaller text" })).not.toHaveAttribute("aria-disabled");
    fireEvent.click(larger);
    expect(onChange).not.toHaveBeenCalled();
    // the % resets to 100 %
    fireEvent.click(screen.getByRole("menuitem", { name: "Text size 150%, reset to 100%" }));
    expect(onChange).toHaveBeenLastCalledWith(1);

    onChange.mockClear();
    rerender(<TextSizeRow size={0.9} onChange={onChange} />);
    const smaller = screen.getByRole("menuitem", { name: "Smaller text" });
    expect(smaller).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(smaller);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("the buttons are keyboard buttons (Enter / Space) and stay focusable at the ends", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TextSizeRow size={1.3} onChange={onChange} />);
    const larger = screen.getByRole("menuitem", { name: "Larger text" });
    expect(larger.tagName).toBe("BUTTON");
    larger.focus();
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenLastCalledWith(1.5);
    expect(larger).not.toBeDisabled();
  });
});

describe("start code boxes", () => {
  it("centred on the Enter the start code card, not elsewhere", () => {
    const { unmount } = render(
      <MemoryRouter>
        <StartCodeForm examId="e1" centered legend="Start code" />
      </MemoryRouter>,
    );
    const boxes = screen.getByTestId("start-code-boxes");
    expect(boxes).toHaveClass("stu-code", "centered");
    expect(within(boxes).getAllByRole("textbox")).toHaveLength(6);
    unmount();
    render(
      <MemoryRouter>
        <StartCodeForm examId="e1" />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("start-code-boxes")).not.toHaveClass("centered");
  });
});
