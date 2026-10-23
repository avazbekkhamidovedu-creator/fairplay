import type { Plugin } from "vite";
export interface PackManifest {
  version: string;
  files: Record<string, string>;
}
export const PYODIDE_FILES: string[];
export const TYPES: Record<string, string>;
export function contentType(name: string): string;
export function packSources(nodeModules?: string): Record<string, string>;
export function sha256(buf: Uint8Array | string): string;
export function packVersion(files: Record<string, string>): string;
export function buildManifest(sources: Record<string, string>): PackManifest;
export function manifestJson(manifest: PackManifest): string;
export function writePack(outDir: string, sources?: Record<string, string>, manifest?: PackManifest): PackManifest;
export function appAssets(): Plugin;
