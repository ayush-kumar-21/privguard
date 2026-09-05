import { CaretLineLeft, CaretLineRight } from '@phosphor-icons/react'
import { NavLink } from 'react-router-dom'

import { cn } from '../../lib/cn'
import { useAuth } from '../../stores/auth'
import { useUI } from '../../stores/ui'
import { Tooltip } from '../ui/misc'
import { RoleCard } from './RoleCard'
import { Wordmark } from './Wordmark'
import { NAV, type NavItem } from './nav'

function NavRow({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const Icon = item.icon
  const link = (
    <NavLink
      to={item.to}
      end={item.end}
      className={({ isActive }) =>
        cn(
          'group relative flex items-center gap-2.5 rounded-sm py-1.5 text-small transition-colors duration-150',
          collapsed ? 'justify-center px-0' : 'px-2.5',
          isActive
            ? 'bg-accent-weak font-medium text-accent'
            : 'text-muted hover:bg-surface-2 hover:text-text',
        )
      }
    >
      {({ isActive }) => (
        <>
          {/* Active marker sits in the sidebar gutter, not inside the row. */}
          <span
            aria-hidden
            className={cn(
              'absolute -left-3 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-accent',
              'transition-opacity duration-150',
              isActive ? 'opacity-100' : 'opacity-0',
            )}
          />
          <Icon
            size={16}
            weight={isActive ? 'fill' : 'regular'}
            className="shrink-0"
            aria-hidden
          />
          {collapsed ? null : <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )

  return collapsed ? (
    <Tooltip content={item.label} side="right">
      <div>{link}</div>
    </Tooltip>
  ) : (
    link
  )
}

export function SidebarNav({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean
  onNavigate?: () => void
}) {
  const hasRole = useAuth((state) => state.hasRole)

  const groups = NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => hasRole(...item.roles)),
  })).filter((group) => group.items.length > 0)

  return (
    <nav
      aria-label="Primary"
      className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 py-4"
      onClick={onNavigate}
    >
      {groups.map((group, index) => (
        <div key={group.label ?? `group-${index}`} className="grid gap-0.5">
          {group.label && !collapsed ? (
            <p className="label-micro px-2.5 pb-1.5">{group.label}</p>
          ) : null}
          {group.label && collapsed ? (
            <div className="mx-auto mb-1 h-px w-5 bg-border" aria-hidden />
          ) : null}
          {group.items.map((item) => (
            <NavRow key={item.to} item={item} collapsed={collapsed} />
          ))}
        </div>
      ))}
    </nav>
  )
}

/** Desktop rail: 240px expanded, 60px icons only. */
export function Sidebar() {
  const collapsed = useUI((state) => state.sidebarCollapsed)
  const toggleSidebar = useUI((state) => state.toggleSidebar)

  return (
    <aside
      className={cn(
        'sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-border bg-surface',
        'transition-[width] duration-200 ease-out md:flex',
        collapsed ? 'w-[60px]' : 'w-[240px]',
      )}
    >
      <div
        className={cn(
          'flex h-16 shrink-0 items-center border-b border-border',
          collapsed ? 'justify-center px-0' : 'px-4',
        )}
      >
        <Wordmark compact={collapsed} />
      </div>

      <SidebarNav collapsed={collapsed} />

      {/* Collapsed to a 60px rail there is no room for prose, and the tooltips
          on the nav rows already carry the labels. */}
      {collapsed ? null : <RoleCard />}

      <div
        className={cn(
          'flex shrink-0 items-center border-t border-border px-3 py-2.5',
          collapsed ? 'justify-center' : 'justify-between',
        )}
      >
        {collapsed ? null : (
          <span className="num text-micro text-faint">safehealth</span>
        )}
        <Tooltip content={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} side="right">
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="rounded-sm p-1.5 text-faint transition-colors hover:bg-surface-2 hover:text-text"
          >
            {collapsed ? (
              <CaretLineRight size={14} aria-hidden />
            ) : (
              <CaretLineLeft size={14} aria-hidden />
            )}
          </button>
        </Tooltip>
      </div>
    </aside>
  )
}
