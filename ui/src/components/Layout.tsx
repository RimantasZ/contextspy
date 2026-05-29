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
import { NavLink, Outlet } from 'react-router-dom'
import { useWebSocket } from '../api/useWebSocket'

const navItems = [
  { to: '/', label: 'Overview', exact: true },
  { to: '/requests', label: 'All Requests' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout() {
  useWebSocket()

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      {/* Sidebar */}
      <nav className="w-48 flex-shrink-0 bg-gray-900 flex flex-col py-6 px-3 gap-1">
        <div className="text-lg font-bold text-indigo-400 px-3 mb-2">ContextSpy</div>
        <div className="px-3 mb-4">
          <img src="/logo.svg" alt="ContextSpy logo" className="w-full h-auto opacity-80" />
        </div>
        {navItems.map(({ to, label, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) =>
              `rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-white'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
