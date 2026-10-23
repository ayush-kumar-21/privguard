import { CaretDown, List, SignOut, User } from '@phosphor-icons/react'
import { useNavigate } from 'react-router-dom'

import { cn } from '../../lib/cn'
import { useBudget } from '../../hooks/useBudget'
import { useAuth } from '../../stores/auth'
import { useUI } from '../../stores/ui'
import { BudgetMiniMeter } from '../privacy/RhoMeter'
import { Badge } from '../ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu'
import { Separator } from '../ui/misc'
import { Skeleton } from '../states'
import { ThemeToggle } from './ThemeToggle'

const ROLE_LABEL: Record<string, string> = {
  dp_analyst: 'analyst',
  clinician: 'clinician',
  auditor: 'auditor',
  dba: 'dba',
}

export function Topbar({ title, elevated = false }: { title: string; elevated?: boolean }) {
  const principal = useAuth((state) => state.principal)
  const logout = useAuth((state) => state.logout)
  const setDrawer = useUI((state) => state.setDrawer)
  const navigate = useNavigate()
  const { data: budget, isPending } = useBudget()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <header
      className={cn(
        'sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 px-4 sm:px-6',
        'border-b bg-bg/85 backdrop-blur-md transition-colors duration-200 ease-out',
        // At the top of the page there is nothing to separate the bar from, so
        // the rule only appears once content has slid underneath it.
        elevated ? 'border-border' : 'border-transparent',
      )}
    >
      <button
        type="button"
        onClick={() => setDrawer(true)}
        aria-label="Open navigation"
        className="-ml-1 rounded-sm p-2 text-muted transition-colors hover:bg-surface-2 hover:text-text md:hidden"
      >
        <List size={16} aria-hidden />
      </button>

      <h1 className="min-w-0 truncate text-h3 text-text">{title}</h1>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {isPending ? (
          <Skeleton className="hidden h-4 w-36 sm:block" />
        ) : budget ? (
          <>
            <BudgetMiniMeter
              remaining={budget.rho_remaining}
              total={budget.rho_total}
              className="hidden sm:flex"
            />
            {budget.queries_remaining !== null ? (
              <span
                className={cn(
                  'num hidden text-small sm:inline',
                  budget.queries_remaining === 0 ? 'text-danger' : 'text-muted',
                )}
                title="Runs your administrator has allowed you"
              >
                {budget.queries_remaining} / {budget.max_queries} runs left
              </span>
            ) : null}
          </>
        ) : null}

        <Separator orientation="vertical" className="hidden h-6 sm:block" />
        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger className="flex items-center gap-2 rounded-sm py-1 pl-1 pr-1.5 text-small text-text transition-colors hover:bg-surface-2">
            <span className="grid h-6 w-6 place-items-center rounded-full bg-accent-weak text-accent">
              <User size={12} weight="bold" aria-hidden />
            </span>
            <span className="hidden max-w-[10ch] truncate sm:inline">
              {principal?.username ?? 'account'}
            </span>
            <CaretDown size={11} className="text-faint" aria-hidden />
          </DropdownMenuTrigger>

          <DropdownMenuContent align="end" className="min-w-[13rem]">
            <DropdownMenuLabel>signed in as</DropdownMenuLabel>
            <div className="px-2.5 pb-2">
              <p className="truncate text-small font-medium text-text">
                {principal?.username}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {principal?.roles.map((role) => (
                  <Badge key={role} tone="outline">
                    {ROLE_LABEL[role] ?? role}
                  </Badge>
                ))}
              </div>
            </div>

            {budget ? (
              <>
                <DropdownMenuSeparator />
                <div className="px-2.5 py-1.5 sm:hidden">
                  <BudgetMiniMeter
                    remaining={budget.rho_remaining}
                    total={budget.rho_total}
                  />
                </div>
              </>
            ) : null}

            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={handleLogout} className="text-danger">
              <SignOut size={14} aria-hidden />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
