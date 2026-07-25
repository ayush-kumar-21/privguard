import * as SelectPrimitive from '@radix-ui/react-select'
import { Check, CaretUpDown } from '@phosphor-icons/react'
import { forwardRef } from 'react'

import { cn } from '../../lib/cn'

export const SelectRoot = SelectPrimitive.Root
export const SelectValue = SelectPrimitive.Value

export const SelectTrigger = forwardRef<
  HTMLButtonElement,
  SelectPrimitive.SelectTriggerProps & { size?: 'sm' | 'md' }
>(function SelectTrigger({ className, children, size = 'md', ...props }, ref) {
  return (
    <SelectPrimitive.Trigger
      ref={ref}
      className={cn(
        'inline-flex w-full items-center justify-between gap-2 rounded-sm border border-border',
        'bg-surface text-left text-text transition-colors duration-150',
        'hover:border-border-strong focus:border-accent',
        'data-[placeholder]:text-faint disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'h-8 px-2.5 text-micro' : 'h-9 px-3 text-small',
        className,
      )}
      {...props}
    >
      <span className="truncate">{children}</span>
      <SelectPrimitive.Icon asChild>
        <CaretUpDown size={13} className="shrink-0 text-faint" aria-hidden />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  )
})

export const SelectContent = forwardRef<HTMLDivElement, SelectPrimitive.SelectContentProps>(
  function SelectContent({ className, children, position = 'popper', ...props }, ref) {
    return (
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          ref={ref}
          position={position}
          sideOffset={4}
          className={cn(
            'z-50 min-w-[9rem] overflow-hidden rounded-sm border border-border bg-surface',
            'shadow-lg shadow-black/10 dark:shadow-black/40',
            'data-[state=open]:animate-slide-down',
            position === 'popper' && 'max-h-72 w-[var(--radix-select-trigger-width)]',
            className,
          )}
          {...props}
        >
          <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    )
  },
)

export const SelectItem = forwardRef<HTMLDivElement, SelectPrimitive.SelectItemProps>(
  function SelectItem({ className, children, ...props }, ref) {
    return (
      <SelectPrimitive.Item
        ref={ref}
        className={cn(
          'relative flex cursor-pointer select-none items-center rounded-[6px] py-1.5 pl-2.5 pr-7',
          'text-small text-text outline-none transition-colors duration-100',
          'data-[highlighted]:bg-surface-2 data-[state=checked]:text-accent',
          'data-[disabled]:pointer-events-none data-[disabled]:opacity-45',
          className,
        )}
        {...props}
      >
        <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
        <SelectPrimitive.ItemIndicator className="absolute right-2 flex items-center">
          <Check size={13} weight="bold" aria-hidden />
        </SelectPrimitive.ItemIndicator>
      </SelectPrimitive.Item>
    )
  },
)

/**
 * Radix reserves the empty string to mean "nothing selected", so an item may
 * not use it. Filter bars naturally want an "any" option worth `''`, so the
 * empty value is swapped for this sentinel on the way in and restored on the
 * way out. Call sites keep using `''`.
 */
const ANY = '__any__'

/** Small controlled select used across filter bars and the policy grid. */
export function Select({
  value,
  onValueChange,
  options,
  placeholder,
  size = 'md',
  className,
  disabled,
  'aria-label': ariaLabel,
}: {
  value: string
  onValueChange: (value: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  size?: 'sm' | 'md'
  className?: string
  disabled?: boolean
  'aria-label'?: string
}) {
  return (
    <SelectRoot
      value={value === '' ? ANY : value}
      onValueChange={(next) => onValueChange(next === ANY ? '' : next)}
      disabled={disabled}
    >
      <SelectTrigger size={size} className={className} aria-label={ariaLabel}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem
            key={option.value || ANY}
            value={option.value === '' ? ANY : option.value}
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectRoot>
  )
}
