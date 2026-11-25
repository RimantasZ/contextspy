import type { MediaItem, RequestBlock } from '../api/client'

/** Images, files and other binary parts the API found in a block (none for plain text). */
export function mediaItems(block: RequestBlock): MediaItem[] {
  const media = block.attrs?.media
  return Array.isArray(media) ? (media as MediaItem[]) : []
}

/** "2 images", "1 image, 1 document"; empty for plain text blocks. */
export function mediaSummary(block: RequestBlock): string {
  const counts = new Map<string, number>()
  for (const item of mediaItems(block)) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1)
  return [...counts]
    .map(([kind, count]) => `${count} ${kind}${count > 1 && kind !== 'audio' ? 's' : ''}`)
    .join(', ')
}

export function mediaEstimateLabel(item: MediaItem): string {
  if (item.method === 'unavailable') return 'not estimated'
  const tokens = `${item.tokens.toLocaleString()} tokens`
  return item.method === 'rough' ? `~${tokens} (rough: no published rule for this model)` : `${tokens} (estimated)`
}

export function mediaDescription(item: MediaItem): string {
  return [
    item.kind,
    item.name,
    item.mime?.split('/').pop(),
    item.width && item.height ? `${item.width}×${item.height}` : undefined,
    item.bytes != null ? `${Math.max(1, Math.round(item.bytes / 1024)).toLocaleString()} KB` : undefined,
  ].filter(Boolean).join(' · ')
}
