import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LeadSelect, type LeadSelectOption } from './LeadSelect'

const options: LeadSelectOption[] = [
  { agentId: 'agent_a', displayName: '甲', avatarRef: null, teamRole: '后端工程师', selectable: true },
  { agentId: 'agent_b', displayName: '乙', avatarRef: null, teamRole: '前端工程师', selectable: false, statusLabel: '暂不在场' }
]

function render(props: Partial<Parameters<typeof LeadSelect>[0]> = {}): string {
  return renderToStaticMarkup(createElement(LeadSelect, {
    options,
    value: '',
    disabled: false,
    onChange: () => undefined,
    ariaLabel: '队伍队长',
    listAriaLabel: '队伍队长候选',
    ...props
  }))
}

// The shared combobox is the settings and Team Preset lead picker. These are
// static-markup assertions of the trigger's accessibility contract; popup
// interaction stays covered by the settings surface tests.
describe('LeadSelect trigger', () => {
  it('exposes a combobox and shows the selected lead with its role', () => {
    const markup = render({ value: 'agent_a' })
    expect(markup).toContain('role="combobox"')
    expect(markup).toContain('aria-label="队伍队长"')
    expect(markup).toContain('aria-haspopup="listbox"')
    expect(markup).toContain('aria-expanded="false"')
    expect(markup).toContain('甲')
    expect(markup).toContain('后端工程师')
  })

  it('keeps an unavailable current lead visible with its status instead of replacing it', () => {
    const markup = render({ value: 'agent_b' })
    expect(markup).toContain('乙')
    expect(markup).toContain('暂不在场')
  })

  it('shows the placeholder and hint when no lead is selected', () => {
    const markup = render({ value: '', placeholder: '选择队长', valueHint: '从已选队员中选择' })
    expect(markup).toContain('选择队长')
    expect(markup).toContain('从已选队员中选择')
  })

  it('disables the trigger when the caller disables it', () => {
    expect(render({ value: 'agent_a', disabled: true })).toContain('disabled=""')
  })
})
