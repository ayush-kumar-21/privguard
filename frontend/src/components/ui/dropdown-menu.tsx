import * as MenuPrimitive from '@radix-ui/react-dropdown-menu'
import { forwardRef } from 'react'

import { cn } from '../../lib/cn'

export const DropdownMenu = MenuPrimitive.Root
export const DropdownMenuTrigger = MenuPrimitive.Trigger

export const DropdownMenuContent = forwardRef<HTMLDivElement, MenuPrimitive.DropdownMenuContentProps>(
  function DropdownMenuContent({ className, sideOffset = 6, ...props }, ref) {
    return (
      <MenuPrimitive.Portal>
        <MenuPrimitive.Content
          ref={ref}
          sideOffset={sideOffset}
          className={cn(
            'z-50 min-w-[11rem] overflow-hidden rounded-sm border border-border bg-surface p-1',
            'shadow-lg shadow-black/10 dark:shadow-black/40',
            'data-[state=open]:animate-slide-down',
            className,
          )}
          {...props}
        />
      </MenuPrimitive.Portal>
    )
  },
)

export const DropdownMenuItem = forwardRef<HTMLDivElement, MenuPrimitive.DropdownMenuItemProps>(
  function DropdownMenuItem({ className, ...props }, ref) {
    return (
      <MenuPrimitive.Item
        ref={ref}
        className={cn(
          'flex cursor-pointer select-none items-center gap-2 rounded-[6px] px-2.5 py-1.5',
          'text-small text-text outline-none transition-colors duration-100',
          'data-[highlighted]:bg-surface-2',
          'data-[disabled]:pointer-events-none data-[disabled]:opacity-45',
          className,
        )}
        {...props}
      />
    )
  },
)

export function DropdownMenuLabel({
  className,
  ...props
}: MenuPrimitive.DropdownMenuLabelProps) {
  return (
    <MenuPrimitive.Label
      className={cn('px-2.5 pb-1 pt-1.5 text-micro text-faint', className)}
      {...props}
    />
  )
}

export function DropdownMenuSeparator({
  className,
  ...props
}: MenuPrimitive.DropdownMenuSeparatorProps) {
  return (
    <MenuPrimitive.Separator
      className={cn('my-1 h-px bg-border', className)}
      {...props}
    />
  )
}
