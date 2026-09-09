import type { HTMLAttributes, ReactNode } from 'react'

export function Panel({ children, className = '', ...props }: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return <div className={`panel ${className}`} {...props}>{children}</div>
}
