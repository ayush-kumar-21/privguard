import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

/**
 * Axios instance for the PrivGuard API.
 *
 * The access token lives in memory only. The refresh token is persisted to
 * localStorage so a page reload keeps the session; that is a deliberate
 * course-scope tradeoff and is XSS-exposed, which is why nothing else about
 * the principal is cached client side.
 */

const REFRESH_KEY = 'privguard.refresh'

let accessToken: string | null = null
/** Called when a refresh fails, so the auth store can tear the session down. */
let onSessionLost: (() => void) | null = null

export const tokens = {
  get access() {
    return accessToken
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set(access: string, refresh?: string) {
    accessToken = access
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    accessToken = null
    localStorage.removeItem(REFRESH_KEY)
  },
}

export function onUnauthenticated(handler: () => void) {
  onSessionLost = handler
}

// Relative '/api' works in dev (Vite proxy) and prod (nginx proxy).
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api',
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

type RetriableConfig = AxiosRequestConfig & { _retried?: boolean }

/** In-flight refresh, shared so a burst of 401s triggers exactly one call. */
let refreshInFlight: Promise<string> | null = null

async function refreshAccess(): Promise<string> {
  const refresh = tokens.refresh
  if (!refresh) throw new Error('no refresh token')

  refreshInFlight ??= axios
    .post(
      `${api.defaults.baseURL}/auth/refresh/`,
      { refresh },
      { headers: { 'Content-Type': 'application/json' } },
    )
    .then((r) => {
      // SimpleJWT rotates the refresh token when ROTATE_REFRESH_TOKENS is on.
      tokens.set(r.data.access, r.data.refresh)
      return r.data.access as string
    })
    .finally(() => {
      refreshInFlight = null
    })

  return refreshInFlight
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as RetriableConfig | undefined
    const isAuthCall = original?.url?.includes('/auth/')

    if (error.response?.status === 401 && original && !original._retried && !isAuthCall) {
      original._retried = true
      try {
        const fresh = await refreshAccess()
        original.headers = { ...original.headers, Authorization: `Bearer ${fresh}` }
        return api.request(original)
      } catch {
        tokens.clear()
        onSessionLost?.()
      }
    }
    return Promise.reject(error)
  },
)

/**
 * Pull a human-readable message out of a DRF error body. The DP gateway
 * answers `{error}`, serializers answer `{field: [msg]}`, SimpleJWT answers
 * `{detail}`.
 */
export function apiError(error: unknown, fallback = 'Something went wrong.'): string {
  if (!axios.isAxiosError(error)) return fallback
  const data = error.response?.data as Record<string, unknown> | string | undefined

  if (typeof data === 'string') return data.slice(0, 400)
  if (data && typeof data === 'object') {
    for (const key of ['error', 'detail', 'non_field_errors']) {
      const value = data[key]
      if (typeof value === 'string') return value
      if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
    }
    const [field, value] = Object.entries(data)[0] ?? []
    if (field) {
      const text = Array.isArray(value) ? value[0] : value
      if (typeof text === 'string') return `${field}: ${text}`
    }
  }
  if (error.code === 'ERR_NETWORK') return 'Cannot reach the API. Is the backend running?'
  return error.message || fallback
}

export default api
