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
import { useEffect, useState } from 'react'

const MINUTE_MS = 60_000

export function useNow(sinceMs: number): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const schedule = () => {
      const age = Date.now() - sinceMs
      timer = setTimeout(() => {
        setNow(Date.now())
        schedule()
      }, age < MINUTE_MS ? 1000 : MINUTE_MS)
    }
    schedule()
    return () => clearTimeout(timer)
  }, [sinceMs])

  return now
}
