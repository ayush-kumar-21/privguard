import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import api, { apiError } from '../../lib/api'
import { InlineError } from '../states'
import { Button } from '../ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from '../ui/dialog'
import { Field, Input } from '../ui/input'
import { Select } from '../ui/select'
import { Switch } from '../ui/misc'

export interface FormField {
  name: string
  label: string
  type: 'text' | 'number' | 'date' | 'datetime' | 'select' | 'boolean'
  options?: { value: string; label: string }[]
  required?: boolean
  description?: string
  step?: string
}

type Values = Record<string, string | boolean>

function initialValues(fields: FormField[], row?: Record<string, unknown>): Values {
  const values: Values = {}
  for (const field of fields) {
    const raw = row?.[field.name]
    if (field.type === 'boolean') {
      values[field.name] = Boolean(raw)
    } else if (field.type === 'datetime' && typeof raw === 'string') {
      // <input type="datetime-local"> wants YYYY-MM-DDTHH:mm, no zone.
      values[field.name] = raw.slice(0, 16)
    } else {
      values[field.name] = raw === null || raw === undefined ? '' : String(raw)
    }
  }
  return values
}

/** Strip empties so a blank optional field is omitted rather than sent as "". */
function toPayload(fields: FormField[], values: Values) {
  const payload: Record<string, unknown> = {}
  for (const field of fields) {
    const value = values[field.name]
    if (field.type === 'boolean') {
      payload[field.name] = Boolean(value)
      continue
    }
    if (value === '') {
      if (field.required) payload[field.name] = value
      continue
    }
    payload[field.name] = field.type === 'number' ? Number(value) : value
  }
  return payload
}

/**
 * Create/edit dialog shared by every data screen. Validation is deliberately
 * thin: the API is the authority, and its errors are surfaced verbatim under
 * the field or above the footer.
 */
export function ResourceForm({
  open,
  onOpenChange,
  title,
  description,
  fields,
  endpoint,
  row,
  idKey,
  invalidateKey,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  fields: FormField[]
  endpoint: string
  /** Present for edit, absent for create. */
  row?: Record<string, unknown>
  idKey: string
  invalidateKey: readonly unknown[]
}) {
  const [values, setValues] = useState<Values>(() => initialValues(fields, row))
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    if (open) {
      setValues(initialValues(fields, row))
      setError(null)
    }
  }, [open, fields, row])

  const save = useMutation({
    mutationFn: async () => {
      const payload = toPayload(fields, values)
      if (row) {
        const { data } = await api.patch(`${endpoint}${row[idKey]}/`, payload)
        return data
      }
      const { data } = await api.post(endpoint, payload)
      return data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [invalidateKey[0]] })
      toast.success(row ? 'Record updated' : 'Record created')
      onOpenChange(false)
    },
    onError: (err) => setError(apiError(err, 'The record could not be saved.')),
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    save.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader title={title} description={description} />
        <form onSubmit={handleSubmit}>
          <DialogBody className="grid gap-4 sm:grid-cols-2">
            {fields.map((field) => {
              const id = `field-${field.name}`
              const value = values[field.name]

              if (field.type === 'boolean') {
                return (
                  <div
                    key={field.name}
                    className="flex items-center justify-between gap-4 rounded-sm border border-border px-3 py-2 sm:col-span-2"
                  >
                    <label htmlFor={id} className="text-small text-text">
                      {field.label}
                    </label>
                    <Switch
                      id={id}
                      checked={Boolean(value)}
                      onCheckedChange={(checked) =>
                        setValues((current) => ({ ...current, [field.name]: checked }))
                      }
                    />
                  </div>
                )
              }

              if (field.type === 'select') {
                return (
                  <Field
                    key={field.name}
                    label={field.label}
                    htmlFor={id}
                    description={field.description}
                  >
                    <Select
                      value={String(value)}
                      onValueChange={(next) =>
                        setValues((current) => ({ ...current, [field.name]: next }))
                      }
                      options={field.options ?? []}
                      placeholder="Select"
                      aria-label={field.label}
                    />
                  </Field>
                )
              }

              return (
                <Field
                  key={field.name}
                  label={field.label}
                  htmlFor={id}
                  description={field.description}
                  hint={field.required ? undefined : 'optional'}
                >
                  <Input
                    id={id}
                    type={
                      field.type === 'number'
                        ? 'number'
                        : field.type === 'date'
                          ? 'date'
                          : field.type === 'datetime'
                            ? 'datetime-local'
                            : 'text'
                    }
                    step={field.step}
                    required={field.required}
                    value={String(value)}
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field.name]: event.target.value,
                      }))
                    }
                  />
                </Field>
              )
            })}

            {error ? <InlineError message={error} className="sm:col-span-2" /> : null}
          </DialogBody>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={save.isPending}>
              {row ? 'Save changes' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** Destructive confirm. Names the row so a mis-click is visible before it lands. */
export function ConfirmDelete({
  open,
  onOpenChange,
  endpoint,
  id,
  label,
  invalidateKey,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  endpoint: string
  id: string | number
  label: string
  invalidateKey: readonly unknown[]
}) {
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const remove = useMutation({
    mutationFn: () => api.delete(`${endpoint}${id}/`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: [invalidateKey[0]] })
      toast.success(`Deleted ${label}`)
      onOpenChange(false)
    },
    onError: (err) => setError(apiError(err, 'The record could not be deleted.')),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent width="max-w-md">
        <DialogHeader
          title="Delete record"
          description="This cannot be undone. Rows referenced by a visit are protected by the database."
        />
        <DialogBody className="grid gap-3">
          <p className="text-small text-muted">
            Delete <span className="num text-text">{label}</span>?
          </p>
          {error ? <InlineError message={error} /> : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
