import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./new-conversation-team-mode.css', import.meta.url), 'utf8')

// No browser is available here, so the confirmed New Conversation summary
// layout is pinned at the CSS contract level.
describe('New Conversation preset summary layout contract', () => {
  it('uses a fixed 2-column member tile grid', () => {
    expect(css).toContain('.new-camp-preset-members {')
    expect(css).toContain('grid-template-columns: repeat(2, minmax(0, 1fr));')
  })

  it('shows 4 rows (8 members) then scrolls only the member grid', () => {
    expect(css).toContain('max-height: 272px;')
    expect(css).toContain('overflow-y: auto;')
    expect(css).toContain('overscroll-behavior: contain;')
  })

  it('is a plain container rather than the previous gray block', () => {
    const summaryStart = css.indexOf('.new-camp-preset-summary {')
    const summaryEnd = css.indexOf('}', summaryStart)
    const summaryRule = css.slice(summaryStart, summaryEnd)
    expect(summaryRule).not.toContain('background')
    expect(summaryRule).not.toContain('border')
  })

  it('renders settings-style tiles with a lead badge', () => {
    expect(css).toContain('.new-camp-preset-member {')
    expect(css).toContain('.new-camp-preset-lead-badge {')
  })

  it('fills the form content column without extra horizontal shrink', () => {
    const summaryStart = css.indexOf('.new-camp-preset-summary {')
    const summaryEnd = css.indexOf('}', summaryStart)
    const summaryRule = css.slice(summaryStart, summaryEnd)
    expect(summaryRule).toContain('width: 100%;')
    expect(summaryRule).toContain('min-width: 0;')

    const membersStart = css.indexOf('.new-camp-preset-members {')
    const membersEnd = css.indexOf('}', membersStart)
    const membersRule = css.slice(membersStart, membersEnd)
    expect(membersRule).toContain('width: 100%;')
    expect(membersRule).toContain('min-width: 0;')
    expect(membersRule).toContain('max-width: none;')
    expect(membersRule).toContain('padding: 0;')
    expect(membersRule).not.toContain('padding-right')
    expect(membersRule).not.toContain('scrollbar-gutter')
  })
})
