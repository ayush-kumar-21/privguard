import { ArrowRight } from '@phosphor-icons/react'
import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { Mark } from '../components/app-shell/Wordmark'
import { ThemeToggle } from '../components/app-shell/ThemeToggle'
import { InlineError } from '../components/states'
import { Button } from '../components/ui/button'
import { Field, Input } from '../components/ui/input'
import { apiError } from '../lib/api'
import { useAuth } from '../stores/auth'

/**
 * The demo shortcuts and the public demo password are for the dev server and the local compose demo.
 * A built image hides them unless it is built with VITE_SHOW_DEMO_ACCOUNTS=true, so a public
 * deployment never advertises a password that is written in the repository.
 */
const SHOW_DEMO_ACCOUNTS = import.meta.env.DEV || import.meta.env.VITE_SHOW_DEMO_ACCOUNTS === 'true'

/** Seeded principals from `manage.py seed_demo`, shown so the demo is runnable. */
const DEMO_ACCOUNTS = [
  { username: 'alice', role: 'analyst', note: 'rho 5.00' },
  { username: 'frank', role: 'analyst', note: 'rho 0.20' },
  { username: 'dave', role: 'dba', note: 'full access' },
  { username: 'erin', role: 'auditor', note: 'ledger' },
]

export default function LoginPage() {
  const principal = useAuth((state) => state.principal)
  const booting = useAuth((state) => state.booting)
  const login = useAuth((state) => state.login)
  const navigate = useNavigate()
  const location = useLocation()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (!booting && principal) {
    const from = (location.state as { from?: Location } | null)?.from?.pathname
    return <Navigate to={from ?? '/'} replace />
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login(username.trim(), password)
      navigate('/', { replace: true })
    } catch (err) {
      setError(apiError(err, 'Sign in failed. Check the username and password.'))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="grid min-h-dvh grid-cols-1 bg-bg lg:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
      {/* Brand panel. Quiet grid motif, one statement, no gradient theatre. */}
      <aside className="relative hidden overflow-hidden border-r border-border bg-surface lg:flex lg:flex-col">
        <div className="grid-motif pointer-events-none absolute inset-0 opacity-60" aria-hidden />

        {/* One centred rail: the wordmark and the statement share a left edge,
            and the whole group sits in the middle of the panel instead of
            hugging its left border with half a screen of dead space beside. */}
        <div className="relative mx-auto flex w-full max-w-[34rem] flex-1 flex-col px-6 py-12">
          <div className="flex items-center gap-3">
            <Mark size={20} />
            <span className="headline text-h3 tracking-tight">PrivGuard</span>
          </div>

          {/*
            Headline, claim and proof read as one block rather than three
            things pushed to the corners of the panel. `justify-between` left a
            dead band across the middle at tall viewports; centring the group
            and capping its measure keeps the composition together at any
            height. The cap is in `em` so it resolves against the headline's own
            size: `ch` here would be measured at the parent's 14px and wrap the
            40px display type to three ragged lines.
          */}
          <div className="flex flex-1 flex-col justify-center py-14">
            <div>
              <h1 className="max-w-[12em] text-hero text-text">
                Differentially private query gateway.
              </h1>
              <p className="mt-5 max-w-[46ch] text-body leading-relaxed text-muted">
                Analysts ask questions of SafeHealth. PrivGuard answers with
                calibrated noise, charges a zCDP budget, and writes the cost to a
                hash-chained ledger.
              </p>

              <dl className="mt-10 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border shadow-panel-edge">
                {[
                  { term: 'accounting', value: 'zCDP' },
                  { term: 'mechanisms', value: '3' },
                  { term: 'ledger', value: 'hash-chained' },
                ].map((item) => (
                  <div key={item.term} className="bg-surface px-4 py-3">
                    <dt className="label-micro">{item.term}</dt>
                    <dd className="mt-1 truncate font-mono text-small text-text">
                      {item.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </aside>

      {/* Form panel */}
      <div className="flex flex-col">
        <div className="flex h-16 items-center justify-between px-6 lg:justify-end">
          <div className="flex items-center gap-2.5 lg:hidden">
            <Mark size={18} />
            <span className="headline text-h3">PrivGuard</span>
          </div>
          <ThemeToggle />
        </div>

        <div className="flex flex-1 items-center justify-center px-6 pb-16">
          <div className="w-full max-w-[360px] animate-fade-rise">
            <h2 className="text-display text-text">Sign in</h2>
            <p className="mt-1.5 text-small text-muted">
              Your role decides which surfaces and columns you can reach.
            </p>

            <form onSubmit={handleSubmit} className="mt-7 grid gap-4" noValidate>
              <Field label="Username" htmlFor="username">
                <Input
                  id="username"
                  name="username"
                  autoComplete="username"
                  autoFocus
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  aria-invalid={Boolean(error)}
                />
              </Field>

              <Field label="Password" htmlFor="password">
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(error)}
                />
              </Field>

              {error ? <InlineError message={error} /> : null}

              <Button
                type="submit"
                variant="primary"
                size="lg"
                loading={pending}
                className="mt-1 w-full"
              >
                Sign in
                {pending ? null : <ArrowRight size={14} weight="bold" aria-hidden />}
              </Button>
            </form>

            {SHOW_DEMO_ACCOUNTS ? (
            <div className="mt-10 border-t border-border pt-5">
              <p className="label-micro">demo principals</p>
              <ul className="mt-2.5 grid gap-1">
                {DEMO_ACCOUNTS.map((account) => (
                  <li key={account.username}>
                    <button
                      type="button"
                      onClick={() => {
                        setUsername(account.username)
                        setPassword('privguard123')
                        setError(null)
                      }}
                      className="flex w-full items-center gap-3 rounded-sm px-2 py-1.5 text-left transition-colors hover:bg-surface-2"
                    >
                      <span className="num w-14 shrink-0 text-small text-text">
                        {account.username}
                      </span>
                      <span className="text-micro text-muted">{account.role}</span>
                      <span className="num ml-auto text-micro text-faint">
                        {account.note}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-3 px-2 text-micro text-faint">
                Password for every seeded principal is
                <span className="num"> privguard123</span>.
              </p>
            </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
