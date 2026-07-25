import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { CircleNotch } from '@phosphor-icons/react'
import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

const button = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium ' +
    'transition-colors duration-150 ease-out select-none ' +
    'disabled:pointer-events-none disabled:opacity-45',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-fg hover:bg-accent-hover active:bg-accent-press',
        secondary:
          'bg-surface text-text border border-border hover:bg-surface-2 hover:border-border-strong',
        ghost: 'text-muted hover:bg-surface-2 hover:text-text',
        danger: 'bg-danger text-white hover:opacity-90 active:opacity-80',
        link: 'text-accent hover:underline underline-offset-4 px-0 h-auto',
      },
      size: {
        sm: 'h-8 px-3 text-micro rounded-sm',
        md: 'h-9 px-3.5 text-small rounded-sm',
        lg: 'h-10 px-5 text-body rounded-sm',
        icon: 'h-9 w-9 rounded-sm',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  asChild?: boolean
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild, loading, children, disabled, ...props },
  ref,
) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      ref={ref}
      className={cn(button({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <>
          <CircleNotch size={14} weight="bold" className="animate-spin" aria-hidden />
          {children}
        </>
      ) : (
        children
      )}
    </Comp>
  )
})

export { button as buttonVariants }
