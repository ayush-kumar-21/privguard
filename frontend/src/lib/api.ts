import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

import type { Paginated } from './types'

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
 * Read a whole DRF collection, following `next` until the last page.
 *
 * Screens that render a complete set rather than a page of one (the grant
 * matrix, the policy editors, role and region pickers) cannot just take
 * `results` off page 1: DRF pages at 20, so the 21st grant would vanish from
 * the matrix with nothing on screen to say so. The walk is capped because an
 * unbounded client-side loop against a fact table is its own bug.
 */
export async function fetchAll<T>(
  endpoint: string,
  { params, maxPages = 20 }: { params?: Record<string, unknown>; maxPages?: number } = {},
): Promise<T[]> {
  const rows: T[] = []
  for (let page = 1; page <= maxPages; page += 1) {
    const { data } = await api.get<Paginated<T> | T[]>(endpoint, {
      params: { ...params, page },
    })
    // Pagination can be switched off per-viewset, in which case DRF answers
    // with a bare array and there is nothing left to walk.
    if (Array.isArray(data)) return data
    rows.push(...data.results)
    if (!data.next) break
  }
  return rows
}

/**
 * Pull a human-readable message out of a DRF error body. The DP gateway
 * answers `{error}`, serializers answer `{field: [msg]}`, SimpleJWT answers
 * `{detail}`.
 */
export function apiError(error: unknown, fallback = 'Something went wrong.'): string {
  if (!axios.isAxiosError(error)) return fallback
  const status = error.response?.status
  const data = error.response?.data as Record<string, unknown> | string | undefined

  if (typeof data === 'string') {
    // A 500 under DEBUG answers with Django's HTML traceback page, and a
    // misrouted request can return an SPA index.html. Neither is a message:
    // dumping the markup into an error banner shows the user page source.
    if (/^\s*(<!doctype|<html|<\?xml)/i.test(data)) {
      return status
        ? `The server failed this request (HTTP ${status}). Check the API logs for the traceback.`
        : fallback
    }
    return data.slice(0, 400)
  }
  if (data && typeof data === 'object') {
    // Uniform envelope from api.exceptions: {error: {code, message, details}}.
    const envelope = data.error
    if (envelope && typeof envelope === 'object' && !Array.isArray(envelope)) {
      const { message, details } = envelope as { message?: unknown; details?: unknown }
      if (details && typeof details === 'object' && !Array.isArray(details)) {
        const [field, value] = Object.entries(details)[0] ?? []
        const text = Array.isArray(value) ? value[0] : value
        if (field && typeof text === 'string') return `${field}: ${text}`
      }
      if (typeof message === 'string') return message
    }
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
