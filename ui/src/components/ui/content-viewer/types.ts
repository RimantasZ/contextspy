import type { MutableRefObject } from 'react'

export type ContentMode = 'verbatim' | 'formatted' | 'tokens' | 'structured'
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

export interface CollapsiblePath {
  path: string
  depth: number
}

export type SearchMarkRefs = MutableRefObject<Array<HTMLElement | null>>
