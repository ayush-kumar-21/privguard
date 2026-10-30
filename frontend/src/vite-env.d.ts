/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Override the API origin when the app is not served behind the same proxy. */
  readonly VITE_API_BASE?: string
  /** `gateway` points the UI at the FastAPI gateway; unset keeps the legacy Django API. */
  readonly VITE_BACKEND?: 'gateway'
  /** `true` shows the demo-account shortcuts and the public demo password on the sign-in page. */
  readonly VITE_SHOW_DEMO_ACCOUNTS?: 'true' | 'false'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
