import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { RequestBlock } from '../../api/client'
import { SearchableContentViewer } from '../ui/SearchableContentViewer'
import { BlockInspector } from './BlockInspector'
import { BlockTile } from './BlockTile'

const block: RequestBlock = {
  id: 1, direction: 'input', position: 0, message_index: 0, block_type: 'user_message',
  category: 'current_user_message', content: '[image png 2000×1224 sha256:1a2b3c4d]', content_purged: false,
  token_count: 3168, tool_name: null, tool_call_id: null, source_key: 'user', activity: null, json_path: null,
  file_path: null, linked_call_id: null, linked_definition_id: null, linked_previous_message_id: null,
  first_seen_session_seq: 1,
  attrs: {
    media_only: true,
    media: [
      { kind: 'image', mime: 'image/png', width: 2000, height: 1224, bytes: 204800, tokens: 3168, method: 'published' },
      { kind: 'document', mime: 'application/pdf', name: 'a.pdf', tokens: 0, method: 'unavailable' },
    ],
  },
}

describe('blocks holding images and files', () => {
  it('lists each part with its estimate in the inspector', () => {
    render(<BlockInspector block={block} blocks={[block]} onJump={() => {}} onClear={() => {}} />)
    expect(screen.getByText('image · png · 2000×1224 · 200 KB')).toBeTruthy()
    expect(screen.getByText('3,168 tokens (estimated)')).toBeTruthy()
    expect(screen.getByText('document · a.pdf · pdf')).toBeTruthy()
    expect(screen.getByText('not estimated')).toBeTruthy()
  })

  it('badges the tile and names the contents', () => {
    const { container } = render(
      <BlockTile block={block} id="t" selected={false} tabIndex={0} onSelect={() => {}} onKeyDown={() => {}} />,
    )
    expect(container.querySelector('[data-media-badge]')).toBeTruthy()
    expect(screen.getByRole('button').getAttribute('aria-label')).toContain('contains 1 image, 1 document')
  })

  it('offers no token highlighting for a placeholder', () => {
    render(<SearchableContentViewer title="User content" content={block.content} tokenHighlight={false} note="stands in" />)
    expect(screen.queryByRole('option', { name: 'Highlight tokens' })).toBeNull()
    expect(screen.getByText('stands in')).toBeTruthy()
  })
})
