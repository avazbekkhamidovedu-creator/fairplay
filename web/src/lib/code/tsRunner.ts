// TypeScript runner: the types are stripped (sucrase, no type checking) and the result runs
// in the JavaScript runner. A syntax error is a compile error in the same output channel.
import { JsRunner } from "./jsRunner";
import type { RunResult, Runner } from "./runner";

/** Types removed, everything else untouched (same line numbers, modern syntax kept). */
export async function stripTypes(code: string): Promise<string> {
  const { transform } = await import("sucrase");
  return transform(code, { transforms: ["typescript"], disableESTransforms: true }).code;
}

export function compileErrorText(e: unknown): string {
  const m = e instanceof Error ? e.message : String(e);
  return /^\w*Error\b/.test(m) ? m + "\n" : `SyntaxError: ${m}\n`;
}

export class TsRunner implements Runner {
  readonly language = "typescript" as const;
  private js: Runner;

  constructor(js: Runner = new JsRunner()) {
    this.js = js;
  }

  prepare(): Promise<void> {
    return this.js.prepare();
  }

  async run(code: string, stdin: string, timeLimitMs: number): Promise<RunResult> {
    let js: string;
    try {
      js = await stripTypes(code);
    } catch (e) {
      return { status: "error", stdout: "", stderr: compileErrorText(e), timeMs: 0 };
    }
    return this.js.run(js, stdin, timeLimitMs);
  }

  dispose(): void {
    this.js.dispose();
  }
}
