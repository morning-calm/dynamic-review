/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_REVIEW_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
