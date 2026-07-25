import { create } from 'zustand'

type Theme = 'dark' | 'light'

const THEME_KEY = 'privguard.theme'
const SIDEBAR_KEY = 'privguard.sidebar'

function readTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY)
  if (stored === 'dark' || stored === 'light') return stored
  // Dark-first product, but respect an explicit OS light preference.
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
}

interface UIState {
  theme: Theme
  sidebarCollapsed: boolean
  /** Mobile drawer, separate from the desktop collapse state. */
  drawerOpen: boolean
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  toggleSidebar: () => void
  setDrawer: (open: boolean) => void
}

export const useUI = create<UIState>((set, get) => ({
  theme: readTheme(),
  sidebarCollapsed: localStorage.getItem(SIDEBAR_KEY) === '1',
  drawerOpen: false,

  setTheme(theme) {
    localStorage.setItem(THEME_KEY, theme)
    applyTheme(theme)
    set({ theme })
  },
  toggleTheme() {
    get().setTheme(get().theme === 'dark' ? 'light' : 'dark')
  },
  toggleSidebar() {
    const next = !get().sidebarCollapsed
    localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0')
    set({ sidebarCollapsed: next })
  },
  setDrawer(open) {
    set({ drawerOpen: open })
  },
}))

// Paint the stored theme before React mounts to avoid a flash of the wrong one.
applyTheme(useUI.getState().theme)
