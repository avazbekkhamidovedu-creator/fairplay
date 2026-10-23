// The few Node built-ins the asset-pack tests use (vitest runs in Node; the project has no
// @types/node).
declare module "node:fs" {
  export function mkdtempSync(prefix: string): string;
  export function readFileSync(path: string): Uint8Array;
  export function readFileSync(path: string, encoding: "utf8"): string;
  export function writeFileSync(path: string, data: string | Uint8Array): void;
}
declare module "node:os" {
  export function tmpdir(): string;
}
declare module "node:path" {
  export function join(...parts: string[]): string;
}
