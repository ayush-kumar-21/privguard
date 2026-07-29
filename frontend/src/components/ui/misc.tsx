import * as SeparatorPrimitive from '@radix-ui/react-separator'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as TabsPrimitive from '@radix-ui/react-tabs'
import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { forwardRef, type ReactNode } from 'react'

import { cn } from '../../lib/cn'

// --- Separator -------------------------------------------------------------
export function Separator({
  className,
  ...props
}: SeparatorPrimitive.SeparatorProps) {
  return (
    <SeparatorPrimitive.Root
      className={cn(
        'shrink-0 bg-border',
        props.orientation === 'vertical' ? 'h-full w-px' : 'h-px w-full',
        className,
      )}
      {...props}
    />
  )
}

// --- Switch ----------------------------------------------------------------
export const Switch = forwardRef<HTMLButtonElement, SwitchPrimitive.SwitchProps>(
  function Switch({ className, ...props }, ref) {
    return (
      <SwitchPrimitive.Root
        ref={ref}
        className={cn(
          'peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent',
          'transition-colors duration-150 ease-out',
          'data-[state=checked]:bg-accent data-[state=unchecked]:bg-border-strong',
          'disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        {...props}
      >
        <SwitchPrimitive.Thumb
          className={cn(
            'pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm',
            'transition-transform duration-150 ease-out',
            'data-[state=checked]:translate-x-[18px] data-[state=unchecked]:translate-x-0.5',
          )}
        />
      </SwitchPrimitive.Root>
    )
  },
)

// --- Tabs ------------------------------------------------------------------
export const Tabs = TabsPrimitive.Root

export function TabsList({ className, ...props }: TabsPrimitive.TabsListProps) {
  return (
    <TabsPrimitive.List
      className={cn('flex items-center gap-1 border-b border-border', className)}
      {...props}
    />
  )
}

export function TabsTrigger({ className, ...props }: TabsPrimitive.TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        '-mb-px border-b-2 border-transparent px-3 py-2 text-small font-medium text-muted',
        'transition-colors duration-150 hover:text-text',
        'data-[state=active]:border-accent data-[state=active]:text-text',
        className,
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }: TabsPrimitive.TabsContentProps) {
  return <TabsPrimitive.Content className={cn('pt-4', className)} {...props} />
}

// --- Tooltip ---------------------------------------------------------------
export const TooltipProvider = TooltipPrimitive.Provider

export function Tooltip({
  content,
  children,
  side = 'top',
}: {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
}) {
  if (!content) return <>{children}</>
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className={cn(
            'z-50 max-w-xs rounded-sm border border-border bg-surface px-2.5 py-1.5',
            'text-micro text-text shadow-pop-edge',
            'data-[state=delayed-open]:animate-slide-down',
          )}
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

// --- Keyboard hint ---------------------------------------------------------
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-[5px]',
        'border border-border bg-surface-2 px-1.5 text-[10px] font-medium text-faint',
      )}
    >
      {children}
    </kbd>
  )
}
