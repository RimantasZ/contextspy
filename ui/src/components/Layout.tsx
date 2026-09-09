// Copyright 2026 Rimantas Zukaitis
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useWebSocket } from '../api/useWebSocket'
import { ThemeToggle, useTheme } from './ui/ThemeToggle'

const navItems = [
  { to: '/', label: 'Overview', icon: '◫', exact: true },
  { to: '/requests', label: 'All Requests', icon: '▤' },
  { to: '/sessions', label: 'Sessions', icon: '◉' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
]

export default function Layout() {
  useWebSocket()
  const [menuOpen, setMenuOpen] = useState(false)
  const { theme, toggleTheme } = useTheme()

  const navigation = (
    <>
      <div className="mb-5 flex items-center gap-2 px-2">
        <img src="/logo_small_wl.png" alt="" className="h-8 w-8 rounded-lg bg-[var(--logo-tile)] object-contain p-1" />
        <span className="nav-wordmark text-sm font-bold tracking-tight">ContextSpy</span>
      </div>
      {navItems.map(({ to, label, icon, exact }) => (
        <NavLink
          key={to}
          to={to}
          end={exact}
          onClick={() => setMenuOpen(false)}
          aria-label={label}
          className={({ isActive }) => `nav-link ${isActive ? 'nav-link-active' : ''}`}
        >
          <span aria-hidden="true" className="w-5 shrink-0 text-center text-base">{icon}</span>
          <span className="nav-label whitespace-nowrap">{label}</span>
        </NavLink>
      ))}
      <div className="mt-auto pt-4">
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
      </div>
    </>
  )

  return (
    <div className="min-h-screen min-w-0 bg-[var(--canvas)] text-[var(--text)] lg:flex">
      <header className="surface sticky top-0 z-40 flex h-14 items-center justify-between border-b border-[var(--border)] px-4 lg:hidden">
        <div className="flex items-center gap-2 font-bold">
          <img src="/logo_small_wl.png" alt="" className="h-7 w-7 rounded-md bg-[var(--logo-tile)] object-contain p-1" />
          ContextSpy
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle theme={theme} onToggle={toggleTheme} showLabel={false} />
          <button
            type="button"
            className="app-button h-9 w-10 px-0"
            aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? '×' : '☰'}
          </button>
        </div>
      </header>

      <nav className="fixed inset-y-0 left-0 hidden h-screen w-16 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface-elevated)] px-2 py-5 lg:flex xl:w-48 xl:px-3">
        {navigation}
      </nav>

      {menuOpen && (
        <div className="fixed inset-0 top-14 z-30 bg-[var(--overlay)] lg:hidden" onClick={() => setMenuOpen(false)}>
          <nav
            className="flex h-full w-64 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface-elevated)] p-4 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            {navigation}
          </nav>
        </div>
      )}

      <main className="min-w-0 flex-1 overflow-x-hidden lg:ml-16 xl:ml-48">
        <Outlet />
      </main>
    </div>
  )
}
