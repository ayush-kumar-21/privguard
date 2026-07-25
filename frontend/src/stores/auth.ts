import { create } from 'zustand'

import api, { onUnauthenticated, tokens } from '../lib/api'
import type { Principal, RoleName } from '../lib/types'

interface AuthState {
  principal: Principal | null
  /** True until the first `hydrate()` settles, so routes do not flash to login. */
  booting: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  hydrate: () => Promise<void>
  hasRole: (...roles: RoleName[]) => boolean
}

export const useAuth = create<AuthState>((set, get) => ({
  principal: null,
  booting: true,

  async login(username, password) {
    const { data } = await api.post('/auth/login/', { username, password })
    tokens.set(data.access, data.refresh)
    const me = await api.get<Principal>('/auth/me/')
    set({ principal: me.data, booting: false })
  },

  async logout() {
    const refresh = tokens.refresh
    try {
      // Blacklists the refresh token server side; a failure here is not fatal.
      if (refresh) await api.post('/auth/logout/', { refresh })
    } catch {
      /* already expired or blacklisted */
    }
    tokens.clear()
    set({ principal: null, booting: false })
  },

  /** Trade the persisted refresh token for a session on app load. */
  async hydrate() {
    if (!tokens.refresh) {
      set({ principal: null, booting: false })
      return
    }
    try {
      const { data } = await api.post('/auth/refresh/', { refresh: tokens.refresh })
      tokens.set(data.access, data.refresh)
      const me = await api.get<Principal>('/auth/me/')
      set({ principal: me.data, booting: false })
    } catch {
      tokens.clear()
      set({ principal: null, booting: false })
    }
  },

  /** dba is a superset: per the UI plan the DBA sees every surface. */
  hasRole(...roles) {
    const principal = get().principal
    if (!principal) return false
    if (principal.roles.includes('dba')) return true
    return roles.some((role) => principal.roles.includes(role))
  },
}))

// A refresh failure anywhere in the app ends the session exactly once.
onUnauthenticated(() => useAuth.setState({ principal: null, booting: false }))
