import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PencilSimple, Plus, UsersThree } from '@phosphor-icons/react'
import { useState } from 'react'

import { EmptyState, ErrorState, InlineError, SkeletonRows } from '../../components/states'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from '../../components/ui/dialog'
import { Field, Input } from '../../components/ui/input'
import { PageHeader, Panel, PanelHeader } from '../../components/ui/panel'
import { Table, TBody, TD, TH, THead, TR, TableScroll } from '../../components/ui/table'
import api, { apiError } from '../../lib/api'
import { int, rho } from '../../lib/format'
import { qk } from '../../lib/queryKeys'
import type { Paginated, Role } from '../../lib/types'

interface ManagedUser {
  id: number
  username: string
  is_active: boolean
  roles: string[]
  account: {
    rho_total: number
    rho_spent: number
    rho_remaining: number
    max_queries: number | null
    queries_used: number
    queries_remaining: number | null
  } | null
}

const USERS_KEY = ['users'] as const

/** Create (user === null) or edit one user. Only what the administrator changed is sent. */
const GROUPS: { base: string; title: string }[] = [
  { base: 'dba', title: 'Administration' },
  { base: 'dp_analyst', title: 'Analysis (runs DP queries)' },
  { base: 'auditor', title: 'Oversight (audit log and ledger)' },
  { base: 'clinician', title: 'Clinical (identified records)' },
]

function UserDialog({
  user,
  roleList,
  onClose,
}: {
  user: ManagedUser | null
  roleList: Role[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const editing = user !== null
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [roles, setRoles] = useState<Set<string>>(new Set(user?.roles ?? ['dp_analyst']))
  const [credits, setCredits] = useState(user?.account ? String(user.account.rho_total) : '')
  const [quota, setQuota] = useState(
    user?.account?.max_queries != null ? String(user.account.max_queries) : '',
  )
  const [active, setActive] = useState(user?.is_active ?? true)

  const save = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { roles: [...roles] }
      if (credits.trim() !== '') body.rho_total = Number(credits)
      if (editing) {
        body.is_active = active
        if (password) body.password = password
        if (user?.account || credits.trim() !== '') {
          body.max_queries = quota.trim() === '' ? null : Number(quota)
        }
        return api.patch(`/users/${user.id}/`, body)
      }
      body.username = username.trim()
      body.password = password
      if (quota.trim() !== '') body.max_queries = Number(quota)
      return api.post('/users/', body)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: USERS_KEY })
      void queryClient.invalidateQueries({ queryKey: ['budget-accounts'] })
      onClose()
    },
  })

  const toggle = (role: string) =>
    setRoles((prev) => {
      const next = new Set(prev)
      if (next.has(role)) next.delete(role)
      else next.add(role)
      return next
    })

  const numbersOk =
    (credits.trim() === '' || Number(credits) > 0) &&
    (quota.trim() === '' || (Number.isInteger(Number(quota)) && Number(quota) >= 0))
  const ready =
    numbersOk && roles.size > 0 && (editing || (username.trim().length >= 3 && password.length > 0))

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader
          title={editing ? `Edit ${user.username}` : 'New user'}
          description={
            editing
              ? 'Changes apply on the user’s next request.'
              : 'The user can sign in as soon as you save.'
          }
        />
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (ready) save.mutate()
          }}
        >
          <DialogBody className="grid gap-4">
            {!editing ? (
              <Field label="Username" htmlFor="u-name" description="3-64 characters: letters, digits, . _ -">
                <Input
                  id="u-name"
                  value={username}
                  autoComplete="off"
                  onChange={(e) => setUsername(e.target.value)}
                />
              </Field>
            ) : null}
            <Field
              label={editing ? 'New password' : 'Password'}
              htmlFor="u-pass"
              description={editing ? 'Leave empty to keep the current one.' : 'At least 8 characters, not a common password.'}
            >
              <Input
                id="u-pass"
                type="password"
                value={password}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>

            <fieldset className="grid gap-3">
              <legend className="mb-1 text-small text-text">Roles</legend>
              {GROUPS.map(({ base, title }) => {
                // A group is the base role plus every job role that carries it.
                const inGroup = roleList.filter((r) => r.role_name === base || r.carries === base)
                if (inGroup.length === 0) return null
                return (
                  <div key={base} className="grid gap-1.5">
                    <p className="label-micro">{title}</p>
                    <div className="flex flex-wrap gap-2">
                      {inGroup.map((r) => (
                        <label
                          key={r.role_name}
                          title={r.description ?? undefined}
                          className="inline-flex cursor-pointer items-center gap-2 rounded-sm border border-border px-2.5 py-1.5 text-small hover:bg-surface-2"
                        >
                          <input
                            type="checkbox"
                            checked={roles.has(r.role_name)}
                            onChange={() => toggle(r.role_name)}
                          />
                          <span className="num">{r.role_name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )
              })}
              <p className="text-micro text-faint">
                A job role carries one base role’s access and nothing more. Hover a role for what it is for.
              </p>
            </fieldset>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Credits (rho)"
                htmlFor="u-rho"
                description={
                  user?.account
                    ? `${rho(user.account.rho_spent)} spent so far`
                    : 'Empty = the database default for query roles'
                }
              >
                <Input
                  id="u-rho"
                  inputMode="decimal"
                  value={credits}
                  onChange={(e) => setCredits(e.target.value)}
                  placeholder="e.g. 5"
                />
              </Field>
              <Field label="Run quota" htmlFor="u-quota" description="Empty = unlimited runs">
                <Input
                  id="u-quota"
                  inputMode="numeric"
                  value={quota}
                  onChange={(e) => setQuota(e.target.value)}
                  placeholder="unlimited"
                />
              </Field>
            </div>

            {editing ? (
              <label className="inline-flex items-center gap-2 text-small">
                <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
                Account is active (untick to block sign-in)
              </label>
            ) : null}

            {save.isError ? (
              <InlineError message={apiError(save.error, 'The user could not be saved.')} />
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={save.isPending} disabled={!ready}>
              {editing ? 'Save changes' : 'Create user'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function UsersPage() {
  const [dialog, setDialog] = useState<{ user: ManagedUser | null } | null>(null)

  const users = useQuery({
    queryKey: USERS_KEY,
    queryFn: async () => (await api.get<ManagedUser[]>('/users/')).data,
    refetchInterval: 5_000, // usage moves while analysts work
  })
  const roles = useQuery({
    queryKey: qk.roles(),
    queryFn: async () => {
      const { data } = await api.get<Paginated<Role> | Role[]>('/roles/')
      return Array.isArray(data) ? data : data.results
    },
  })

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Users"
        description="Create users and decide what each one may do: roles, credits (the privacy budget they may spend) and how many runs they get. Users are deactivated rather than deleted, so their ledger and audit history stay intact."
      />

      <Panel inset>
        <PanelHeader
          title="All users"
          description="from sys_principal"
          actions={
            <Button variant="primary" size="sm" onClick={() => setDialog({ user: null })}>
              <Plus size={13} weight="bold" aria-hidden />
              New user
            </Button>
          }
        />
        {users.isError ? (
          <ErrorState
            message={apiError(users.error, 'Users could not be loaded.')}
            onRetry={() => void users.refetch()}
          />
        ) : (
          <TableScroll>
            <Table>
              <THead>
                <tr>
                  <TH>user</TH>
                  <TH>roles</TH>
                  <TH className="text-right">credits left</TH>
                  <TH className="text-right">runs used / allowed</TH>
                  <TH>status</TH>
                  <TH />
                </tr>
              </THead>
              <TBody>
                {users.isPending ? (
                  <SkeletonRows rows={4} widths={['w-24', 'w-32', 'w-16', 'w-20', 'w-16', 'w-10']} />
                ) : users.data.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState icon={UsersThree} title="No users" description="Create the first one." />
                    </td>
                  </tr>
                ) : (
                  users.data.map((u) => (
                    <TR key={u.id}>
                      <TD className="num">{u.username}</TD>
                      <TD>
                        <div className="flex flex-wrap gap-1.5">
                          {u.roles.map((r) => (
                            <Badge key={r} tone="outline">
                              {r}
                            </Badge>
                          ))}
                        </div>
                      </TD>
                      <TD className="num text-right">
                        {u.account ? `${rho(u.account.rho_remaining)} / ${rho(u.account.rho_total, 2)}` : '--'}
                      </TD>
                      <TD className="num text-right">
                        {u.account
                          ? `${int(u.account.queries_used)} / ${u.account.max_queries ?? '∞'}`
                          : '--'}
                      </TD>
                      <TD>
                        <Badge tone={u.is_active ? 'ok' : 'neutral'}>
                          {u.is_active ? 'active' : 'blocked'}
                        </Badge>
                      </TD>
                      <TD className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Edit ${u.username}`}
                          onClick={() => setDialog({ user: u })}
                        >
                          <PencilSimple size={13} aria-hidden />
                          Edit
                        </Button>
                      </TD>
                    </TR>
                  ))
                )}
              </TBody>
            </Table>
          </TableScroll>
        )}
      </Panel>

      {dialog ? (
        <UserDialog
          // A new key per target so the form never shows the previous user's values.
          key={dialog.user?.id ?? 'new'}
          user={dialog.user}
          roleList={roles.data ?? []}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  )
}
