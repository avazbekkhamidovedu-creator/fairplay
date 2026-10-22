// The one Node API the runner tests use (no @types/node in this project).
declare module "node:vm" {
  export function runInNewContext(code: string, context: object, options?: { filename?: string }): unknown;
}
