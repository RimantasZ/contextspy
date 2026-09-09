import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'
const STORAGE_KEY = 'contextspy-theme'

function initialTheme(): Theme {
  const applied = document.documentElement.dataset.theme
  if (applied === 'light' || applied === 'dark') return applied
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* storage can be unavailable */ }
  }, [theme])

  return { theme, toggleTheme: () => setTheme((current) => current === 'light' ? 'dark' : 'light') }
}

export function ThemeToggle({ theme, onToggle, showLabel = true }: { theme: Theme; onToggle: () => void; showLabel?: boolean }) {
  const next = theme === 'light' ? 'dark' : 'light'
  return (
    <button type="button" onClick={onToggle} className={`theme-toggle ${showLabel ? 'w-full' : 'h-9 w-10 px-0'}`} aria-label={`Switch to ${next} mode`} title={`Switch to ${next} mode`}>
      <span aria-hidden="true" className="w-5 text-center">{theme === 'light' ? '☾' : '☀'}</span>
      {showLabel && <span className="nav-label">{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>}
    </button>
  )
}
