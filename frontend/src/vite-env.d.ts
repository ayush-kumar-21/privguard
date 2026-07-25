/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API origin when the app is not served behind the same proxy. */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
