import { describe, expect, it } from "vitest";
import { examInCourse } from "../components/student/Courses";

const exam = (over: Record<string, unknown>) => ({ id: "e1", title: "Quiz", course: "CS 101", ...over }) as never;
const klass = (id: string | number, course = "CS 101 Programming", name = "CS 101 Section A") =>
  ({ id, name, course, source: typeof id === "string" ? "dkuoj" : "fairplay" }) as never;

describe("exam to course matching", () => {
  it("uses class_ids when the server sends them", () => {
    expect(examInCourse(exam({ class_ids: ["dkuoj:31"] }), klass("dkuoj:31"))).toBe(true);
    // same course name, other section: not this card
    expect(examInCourse(exam({ class_ids: ["dkuoj:31"] }), klass("dkuoj:32", "CS 101 Programming", "CS 101 Section B"))).toBe(false);
    expect(examInCourse(exam({ class_ids: ["12"] }), klass(12))).toBe(true);
    expect(examInCourse(exam({ class_ids: [] }), klass(12))).toBe(false);
  });
  it("falls back to the course name without class_ids", () => {
    expect(examInCourse(exam({}), klass("dkuoj:31"))).toBe(true);
  });
});
