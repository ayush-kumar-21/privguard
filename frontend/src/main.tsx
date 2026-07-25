import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'sonner'

import App from './App'
import { TooltipProvider } from './components/ui/misc'
import { useUI } from './stores/ui'
import './index.css'
// To self-host the licensed brand serif, drop the files into public/fonts/ and
// uncomment the next line. See src/styles/anthropic-serif.css.
// import './styles/anthropic-serif.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Data-app defaults: no surprise refetch storms, one retry on flaky reads.
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

function Providers() {
  const theme = useUI((state) => state.theme)
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={220} skipDelayDuration={300}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
        <Toaster
          theme={theme}
          position="bottom-right"
          closeButton
          toastOptions={{
            classNames: {
              toast:
                'rounded-sm border border-border bg-surface text-text text-small shadow-lg',
              description: 'text-muted',
            },
          }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Providers />
  </StrictMode>,
)
