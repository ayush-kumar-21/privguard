import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * In dev, proxy /api to the Django server so the client can use relative URLs,
 * exactly as nginx serves them in production.
 *
 * The target is configurable because docker-compose.override.yml can remap the
 * API off port 8000 when another project on the machine already holds it. Set
 * API_PROXY_TARGET in frontend/.env to match the host port compose published.
 */
export default defineConfig(({ mode }) => {
  // envDir '.' rather than process.cwd(): this config is typechecked by
  // tsconfig.node.json, which carries no @types/node, and Vite resolves the
  // relative path itself.
  const env = loadEnv(mode, '.', '')
  const target = env.API_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: { '/api': target },
    },
  }
})
