// 2026-10-23: Vite's "?url" imports (the bundled pdf.js worker) and import.meta.env.
declare module "*?url" {
  const url: string;
  export default url;
}

interface ImportMetaEnv {
  readonly DEV: boolean;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
