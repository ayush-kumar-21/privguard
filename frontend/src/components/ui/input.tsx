import * as LabelPrimitive from '@radix-ui/react-label'
import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react'

import { cn } from '../../lib/cn'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          'h-9 w-full rounded-sm border border-border bg-surface px-3 text-small text-text',
          'placeholder:text-faint transition-colors duration-150',
          'hover:border-border-strong focus:border-accent',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'aria-[invalid=true]:border-danger',
          className,
        )}
        {...props}
      />
    )
  },
)

export const Label = forwardRef<
  HTMLLabelElement,
  LabelPrimitive.LabelProps & { hint?: ReactNode }
>(function Label({ className, children, hint, ...props }, ref) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <LabelPrimitive.Root
        ref={ref}
        className={cn('text-micro font-medium text-muted', className)}
        {...props}
      >
        {children}
      </LabelPrimitive.Root>
      {hint ? <span className="text-micro text-faint">{hint}</span> : null}
    </div>
  )
})

/**
 * Label above, control in the middle, error below. Never a placeholder as a
 * label: the field keeps its name once the user starts typing.
 */
export function Field({
  label,
  htmlFor,
  error,
  hint,
  description,
  children,
  className,
}: {
  label: string
  htmlFor: string
  error?: string | null
  hint?: ReactNode
  description?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('grid gap-1.5', className)}>
      <Label htmlFor={htmlFor} hint={hint}>
        {label}
      </Label>
      {children}
      {description && !error ? (
        <p className="text-micro text-faint">{description}</p>
      ) : null}
      {error ? (
        <p role="alert" className="text-micro text-danger">
          {error}
        </p>
      ) : null}
    </div>
  )
}
