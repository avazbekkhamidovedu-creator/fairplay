import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";
import type { AnswerValue, StudentQuestion } from "../api/types";
import { CodeQuestion } from "../components/student/CodeQuestion";

beforeAll(() => {
  const rect = { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) };
  Range.prototype.getClientRects ??= (() => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] })) as never;
  Range.prototype.getBoundingClientRect ??= (() => rect) as never;
});

const PY = { key: "PY3", name: "Python 3", runner: "python" };
const CPP = { key: "CPP17", name: "C++17", runner: "cpp" };
const JAVA = { key: "JAVA", name: "Java", runner: null };

function question(languages: object[], extra: object = {}): StudentQuestion {
  return { id: "q_1", type: "code", prompt: "x", points: 5, options: [], prompt_format: "markdown",
    languages, templates: { PY3: "py\n", CPP17: "cpp\n", JAVA: "java\n" }, tests: [], ...extra } as unknown as StudentQuestion;
}

function show(q: StudentQuestion, value?: AnswerValue) {
  render(<CodeQuestion question={q} value={value} onChange={() => undefined} prompt={<h1>x</h1>} attemptId="a_d" />);
}

describe("code language picker", () => {
  it("one language: a label, no picker", () => {
    show(question([CPP], { default_language: "CPP17" }));
    expect(screen.queryByTestId("code-language-picker")).toBeNull();
    expect(screen.getByTestId("code-language")).toHaveTextContent("C++17");
  });

  it("several: the default is selected and listed first", async () => {
    show(question([CPP, JAVA, PY], { default_language: "PY3" }));
    const picker = screen.getByTestId("code-language-picker");
    expect(picker).toHaveValue("PY3");
    expect(Array.from(picker.querySelectorAll("option")).map((o) => o.value)).toEqual(["PY3", "CPP17", "JAVA"]);
    await waitFor(() => expect(document.querySelector(".cm-content")?.textContent).toContain("py"));
  });

  it("no default field: the first language, all stay selectable", () => {
    show(question([JAVA, CPP]));
    const picker = screen.getByTestId("code-language-picker");
    expect(picker).toHaveValue("JAVA");
    expect(picker.querySelectorAll("option")).toHaveLength(2);
  });

  it("a saved answer's language wins over the default", () => {
    show(question([PY, CPP], { default_language: "PY3" }), { language: "CPP17", source: "int main(){}" });
    expect(screen.getByTestId("code-language-picker")).toHaveValue("CPP17");
  });
});
