import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from '@phosphor-icons/react'
import { forwardRef, type ReactNode } from 'react'

import { cn } from '../../lib/cn'

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close

export const DialogContent = forwardRef<
  HTMLDivElement,
  DialogPrimitive.DialogContentProps & { width?: string }
>(function DialogContent({ className, children, width = 'max-w-lg', ...props }, ref) {
  return (
    <DialogPrimitive.Portal>
      {/*
        The overlay is the centring container. Translate-based centring would
        fight the scale-in keyframes, which finish at `transform: none` and
        would drop the dialog off-centre.
      */}
      <DialogPrimitive.Overlay
        className={cn(
          'fixed inset-0 z-50 grid place-items-center overflow-y-auto',
          'bg-black/45 p-4 backdrop-blur-[2px]',
          'data-[state=open]:animate-fade-in',
        )}
      >
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            'relative my-auto w-full',
            'rounded-lg border border-border bg-surface shadow-pop-edge',
            'data-[state=open]:animate-scale-in',
            width,
            className,
          )}
          {...props}
        >
          {children}
          <DialogPrimitive.Close
            className={cn(
              'absolute right-3 top-3 rounded-sm p-1.5 text-faint transition-colors',
              'hover:bg-surface-2 hover:text-text',
            )}
            aria-label="Close"
          >
            <X size={14} weight="bold" aria-hidden />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Overlay>
    </DialogPrimitive.Portal>
  )
})

export function DialogHeader({
  title,
  description,
}: {
  title: ReactNode
  description?: ReactNode
}) {
  return (
    <header className="border-b border-border px-5 py-4">
      <DialogPrimitive.Title className="text-h3 text-text">
        {title}
      </DialogPrimitive.Title>
      {description ? (
        <DialogPrimitive.Description className="mt-1 text-micro text-muted">
          {description}
        </DialogPrimitive.Description>
      ) : null}
    </header>
  )
}

export function DialogBody({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('max-h-[65vh] overflow-y-auto px-5 py-4', className)}>{children}</div>
  )
}

export function DialogFooter({ children }: { children: ReactNode }) {
  return (
    <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
      {children}
    </footer>
  )
}
