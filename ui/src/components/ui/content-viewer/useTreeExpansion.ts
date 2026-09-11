import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CollapsiblePath } from './types'

export function useTreeExpansion(paths: CollapsiblePath[], contentKey: string) {
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(() => new Set())
  const validPaths = useMemo(() => new Set(paths.map((item) => item.path)), [paths])
  const pathSignature = useMemo(() => paths.map((item) => `${item.path}:${item.depth}`).join('|'), [paths])

  useEffect(() => { setCollapsedPaths(new Set()) }, [contentKey])
  useEffect(() => {
    setCollapsedPaths((current) => {
      const next = new Set([...current].filter((path) => validPaths.has(path)))
      return next.size === current.size ? current : next
    })
  }, [pathSignature, validPaths])

  const toggle = useCallback((path: string) => {
    setCollapsedPaths((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else if (validPaths.has(path)) next.add(path)
      return next
    })
  }, [validPaths])
  const expandAll = useCallback(() => setCollapsedPaths(new Set()), [])
  const collapseBelowDepth = useCallback((minimumDepth = 2) => {
    setCollapsedPaths(new Set(paths.filter((item) => item.depth >= minimumDepth).map((item) => item.path)))
  }, [paths])

  return { collapsedPaths, hasCollapsedScopes: collapsedPaths.size > 0, toggle, expandAll, collapseBelowDepth }
}
