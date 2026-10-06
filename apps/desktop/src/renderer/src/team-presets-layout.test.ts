import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css = readFileSync(new URL('./team-presets.css', import.meta.url), 'utf8')

// No browser is available in this suite, so the two layout defects fixed for
// T6 are pinned at the CSS contract level: the workspace must not overlap the
// 50px window drag strip, and the editor dialog must not clip the lead popup.
describe('Team Presets layout contract', () => {
  it('places the teams content below the window drag strip row', () => {
    expect(css).toContain('.content.teams-content { grid-row: 2; }')
    expect(css).not.toContain('grid-row: 1 / -1')
  })

  it('lets the in-dialog lead popup extend past the editor dialog box', () => {
    expect(css).toContain('.dialog-content.app-dialog.team-preset-editor-dialog { overflow: visible; }')
  })

  it('gives the team card member tiles a 4-column, 2-row scrollable grid', () => {
    expect(css).toContain('.team-card-members {')
    expect(css).toContain('grid-template-columns: repeat(4, minmax(0, 1fr));')
    expect(css).toContain('max-height: 132px;')
    expect(css).toContain('overflow-y: auto;')
  })

  it('keeps a fixed member area height and responsive columns on narrow screens', () => {
    expect(css).toContain('.team-card-members { grid-template-columns: repeat(2, minmax(0, 1fr)); }')
    expect(css).toContain('.team-card-members { grid-template-columns: minmax(0, 1fr); }')
  })

  it('centers the team workspace content column', () => {
    expect(css).toContain('width: min(980px, 100%);')
    expect(css).toContain('margin: 0 auto;')
  })

  it('bounds the team editor member list with its own vertical scroll', () => {
    const start = css.indexOf('.team-editor-member-list {')
    const end = css.indexOf('}', start)
    const rule = css.slice(start, end)
    expect(rule).toContain('max-height: 200px;')
    expect(rule).toContain('overflow-y: auto;')
    expect(rule).toContain('overflow-x: hidden;')
    expect(rule).toContain('overscroll-behavior: contain;')
  })

  it('bounds the unavailable-member list too', () => {
    const start = css.indexOf('.team-editor-unavailable ul {')
    const end = css.indexOf('}', start)
    const rule = css.slice(start, end)
    expect(rule).toContain('max-height: 120px;')
    expect(rule).toContain('overflow-y: auto;')
  })
})
