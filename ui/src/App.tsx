import { Routes, Route } from 'react-router-dom'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<div className="p-8 text-2xl font-bold">Token-Scrooge — Phase 0 scaffold</div>} />
    </Routes>
  )
}
