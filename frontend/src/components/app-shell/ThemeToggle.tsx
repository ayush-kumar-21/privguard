import { Moon, Sun } from '@phosphor-icons/react'

import { useUI } from '../../stores/ui'
import { Tooltip } from '../ui/misc'

export function ThemeToggle() {
  const theme = useUI((state) => state.theme)
  const toggleTheme = useUI((state) => state.toggleTheme)
  const next = theme === 'dark' ? 'light' : 'dark'

  return (
    <Tooltip content={`Switch to ${next} theme`}>
      <button
        type="button"
        onClick={toggleTheme}
        aria-label={`Switch to ${next} theme`}
        className="rounded-sm p-2 text-muted transition-colors hover:bg-surface-2 hover:text-text"
      >
        {theme === 'dark' ? (
          <Sun size={15} aria-hidden />
        ) : (
          <Moon size={15} aria-hidden />
        )}
      </button>
    </Tooltip>
  )
}
