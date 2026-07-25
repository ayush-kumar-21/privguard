import { PencilSimple, Trash } from '@phosphor-icons/react'

import { Button } from '../ui/button'
import { Tooltip } from '../ui/misc'

/**
 * Edit and delete for one row. Rendered only where the API would accept the
 * write, so the UI never offers an action that comes back 403.
 */
export function RowActions({
  onEdit,
  onDelete,
  label,
}: {
  onEdit?: () => void
  onDelete?: () => void
  label: string
}) {
  if (!onEdit && !onDelete) return null
  return (
    <div className="flex items-center justify-end gap-0.5">
      {onEdit ? (
        <Tooltip content={`Edit ${label}`}>
          <Button
            size="icon"
            variant="ghost"
            onClick={onEdit}
            aria-label={`Edit ${label}`}
            className="h-7 w-7"
          >
            <PencilSimple size={13} aria-hidden />
          </Button>
        </Tooltip>
      ) : null}
      {onDelete ? (
        <Tooltip content={`Delete ${label}`}>
          <Button
            size="icon"
            variant="ghost"
            onClick={onDelete}
            aria-label={`Delete ${label}`}
            className="h-7 w-7 hover:text-danger"
          >
            <Trash size={13} aria-hidden />
          </Button>
        </Tooltip>
      ) : null}
    </div>
  )
}
