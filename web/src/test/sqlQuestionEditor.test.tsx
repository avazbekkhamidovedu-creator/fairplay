import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Exam } from "../api/types";
import { CodeQuestionEditor } from "../components/teacher/CodeQuestionEditor";
import { draftFromExam, newQuestion, toExamInput, validateDraft, type DraftQuestion } from "../pages/teacher/examDraft";

function Harness({ init, def = "python" }: { init: DraftQuestion; def?: "python" | "sql" | "typescript" | "c" }) {
  const [q, setQ] = useState(init);
  return (
    <>
      <CodeQuestionEditor q={q} id={(s) => `qx-${s}`} errors={{}} onChange={(p) => setQ((x) => ({ ...x, ...p }))} defaultLanguage={def} />
      <output data-testid="state">{JSON.stringify({ language: q.language, setup: q.sqlSetup, compare: q.sqlCompare })}</output>
    </>
  );
}

const code = (over: Partial<DraftQuestion> = {}): DraftQuestion => ({ ...newQuestion("code"), ...over });
const state = () => JSON.parse(screen.getByTestId("state").textContent ?? "{}");

describe("multi-language question editor", () => {
  it("lists every language and disables the ones without a runner", () => {
    render(<Harness init={code()} />);
    const select = screen.getByLabelText("Language");
    const opts = Array.from(select.querySelectorAll("option"));
    expect(opts.map((o) => o.value)).toEqual(["", "python", "javascript", "typescript", "sql", "c", "cpp", "java"]);
    const off = opts.filter((o) => o.disabled).map((o) => o.textContent);
    expect(off).toEqual(["C (runner coming soon)", "C++ (runner coming soon)", "Java (runner coming soon)"]);
  });

  it("keeps a question that already has an unsupported language usable", () => {
    render(<Harness init={code({ language: "java" })} />);
    expect((screen.getByLabelText("Language") as HTMLSelectElement).value).toBe("java");
    expect(screen.getByText(/No runner is available for this language yet/)).toBeInTheDocument();
  });

  it("shows SQL fields only for SQL and hides the test input", async () => {
    const user = userEvent.setup();
    render(<Harness init={code()} />);
    expect(screen.queryByLabelText("Setup script")).toBeNull();
    expect(screen.getAllByText("Input (stdin)").length).toBe(2);
    await user.selectOptions(screen.getByLabelText("Language"), "sql");
    expect(screen.getByLabelText("Setup script")).toBeInTheDocument();
    expect(screen.queryByText("Input (stdin)")).toBeNull();
    expect(screen.getAllByText(/Expected result \(first line: column names/).length).toBe(2);
    await user.selectOptions(screen.getByLabelText("Compare rows"), "ordered");
    expect(state().compare).toBe("ordered");
  });

  it("counts setup characters and flags the limit", async () => {
    const user = userEvent.setup();
    render(<Harness init={code({ language: "sql" })} />);
    await user.click(screen.getByLabelText("Setup script"));
    await user.paste("create table t(a);");
    expect(screen.getByText("18 / 50,000")).toBeInTheDocument();
    expect(state().setup).toBe("create table t(a);");
  });

  it("uses the exam default language for SQL fields and shows the TypeScript note", () => {
    const { unmount } = render(<Harness init={code()} def="sql" />);
    expect(screen.getByLabelText("Setup script")).toBeInTheDocument();
    unmount();
    render(<Harness init={code({ language: "typescript" })} />);
    expect(screen.getByText(/removed and not checked/)).toBeInTheDocument();
  });
});

describe("sql fields in the saved exam", () => {
  const exam = (qs: Record<string, unknown>[]): Exam => ({ title: "t", questions: qs, default_language: "python" }) as unknown as Exam;
  const saved = (d: ReturnType<typeof draftFromExam>) => toExamInput(d).questions as unknown as Record<string, unknown>[];

  it("round-trips sql_setup and sql_compare", () => {
    const d = draftFromExam(exam([{ type: "code", prompt: "p", points: 10, language: "sql", sql_setup: "create table t(a);", sql_compare: "ordered", tests: [] }]));
    expect(d.questions[0].sqlSetup).toBe("create table t(a);");
    const out = saved(d)[0];
    expect(out.sql_setup).toBe("create table t(a);");
    expect(out.sql_compare).toBe("ordered");
  });

  it("never sends sql fields for non-SQL questions, even with stale values", () => {
    const d = draftFromExam(exam([{ type: "code", prompt: "p", points: 10, language: "python", tests: [] }]));
    d.questions[0].sqlSetup = "stale";
    const out = saved(d)[0];
    expect("sql_setup" in out).toBe(false);
    expect("sql_compare" in out).toBe(false);
  });

  it("sends them for a question on an SQL exam default", () => {
    const d = draftFromExam(exam([{ type: "code", prompt: "p", points: 10, tests: [] }]));
    d.defaultLanguage = "sql";
    expect(saved(d)[0].sql_compare).toBe("unordered");
  });

  it("rejects a setup script over 50,000 characters", () => {
    const d = draftFromExam(exam([{ type: "code", prompt: "p", points: 10, language: "sql", sql_setup: "x".repeat(50001), tests: [] }]));
    d.title = "t";
    expect(Object.values(validateDraft(d).byKey)[0].sqlSetup).toMatch(/50,000/);
  });
});
