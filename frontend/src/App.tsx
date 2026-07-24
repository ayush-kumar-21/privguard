import { useEffect, useState } from 'react'
import client from './api/client'

export default function App() {
  const [health, setHealth] = useState<string>('checking…')

  useEffect(() => {
    client
      .get('/health/')
      .then((r) => setHealth(r.data.status))
      .catch(() => setHealth('unreachable'))
  }, [])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <header className="border-b bg-white px-6 py-4 shadow-sm">
        <h1 className="text-xl font-semibold">PrivGuard</h1>
        <p className="text-sm text-slate-500">
          Differentially Private Query Gateway — BCSE307L
        </p>
      </header>

      <main className="mx-auto max-w-3xl p-6">
        <div className="rounded-lg border bg-white p-5 shadow-sm">
          <h2 className="mb-3 font-medium">Backend status</h2>
          <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-sm">
            <span
              className={
                'h-2 w-2 rounded-full ' +
                (health === 'ok' ? 'bg-emerald-500' : 'bg-amber-500')
              }
            />
            API: {health}
          </span>
          <p className="mt-4 text-sm text-slate-500">
            Scaffold ready. Next screens: Login, Budget Dashboard, SQL Console,
            Policy Admin, Audit, Attack Lab.
          </p>
        </div>
      </main>
    </div>
  )
}
