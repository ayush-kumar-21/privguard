import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from '@phosphor-icons/react'
import { Suspense } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { Skeleton } from '../states'
import { useUI } from '../../stores/ui'
import { SidebarNav, Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import { Wordmark } from './Wordmark'
import { NAV_INDEX } from './nav'

function sectionTitle(pathname: string): string {
  // Longest matching prefix wins, so /data/patients beats /.
  const match = [...NAV_INDEX]
    .filter((item) => (item.end ? pathname === item.to : pathname.startsWith(item.to)))
    .sort((a, b) => b.to.length - a.to.length)[0]
  return match?.label ?? 'PrivGuard'
}

/** Mobile navigation drawer. Under 768px the rail becomes an overlay. */
function NavDrawer() {
  const drawerOpen = useUI((state) => state.drawerOpen)
  const setDrawer = useUI((state) => state.setDrawer)

  return (
    <DialogPrimitive.Root open={drawerOpen} onOpenChange={setDrawer}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px] md:hidden" />
        <DialogPrimitive.Content
          className="fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col border-r border-border bg-surface data-[state=open]:animate-slide-down md:hidden"
          aria-label="Navigation"
        >
          <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
          <div className="flex h-16 shrink-0 items-center justify-between border-b border-border px-4">
            <Wordmark />
            <DialogPrimitive.Close
              aria-label="Close navigation"
              className="rounded-sm p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
            >
              <X size={14} weight="bold" aria-hidden />
            </DialogPrimitive.Close>
          </div>
          <SidebarNav collapsed={false} onNavigate={() => setDrawer(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

/** Shape-matched placeholder while a lazily loaded route chunk arrives. */
function RouteFallback() {
  return (
    <div className="grid gap-6">
      <div className="grid gap-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export function AppShell() {
  const { pathname } = useLocation()

  return (
    <div className="flex min-h-dvh bg-bg">
      <Sidebar />
      <NavDrawer />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar title={sectionTitle(pathname)} />
        <main className="flex-1">
          {/* key on pathname so each route mounts with its own fade+rise */}
          <div
            key={pathname}
            className="mx-auto w-full max-w-page animate-fade-rise px-4 py-6 sm:px-6 sm:py-8"
          >
            <Suspense fallback={<RouteFallback />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  )
}
