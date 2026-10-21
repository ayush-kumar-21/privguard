/**
 * Which API the UI is talking to.
 *
 * The default is the legacy Django API, which serves every screen. `VITE_BACKEND=gateway`
 * (see `.env.gateway`, `npm run dev:gateway`) points the app at the FastAPI gateway in front of
 * the Rust engine. The gateway serves auth, the DP console, budget, audit and ledger views; the
 * SafeHealth CRUD pages and the role/grant/policy editors exist only in Django and are hidden
 * in gateway mode until the policy grid (4.9) replaces them.
 */
export const GATEWAY_MODE = import.meta.env.VITE_BACKEND === 'gateway'

