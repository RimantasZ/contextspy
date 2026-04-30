import { NavLink, Outlet } from 'react-router-dom'
import { useWebSocket } from '../api/useWebSocket'

const navItems = [
  { to: '/', label: 'Dashboard', exact: true },
  { to: '/requests', label: 'Requests' },
  { to: '/sessions', label: 'Sessions' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout() {
  useWebSocket()

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      {/* Sidebar */}
      <nav className="w-48 flex-shrink-0 bg-gray-900 flex flex-col py-6 px-3 gap-1">
        <div className="text-lg font-bold text-indigo-400 px-3 mb-4">Token-Scrooge</div>
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
