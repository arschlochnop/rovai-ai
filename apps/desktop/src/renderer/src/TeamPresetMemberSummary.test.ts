import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { TeamPresetMemberSummary } from './TeamPresetMemberSummary'
import type { TeamPresetMemberView } from './team-presets'

const preset = { name: '后端开发', leadAgentId: 'agent_a' }
const members: TeamPresetMemberView[] = [
  { agentId: 'agent_b', displayName: '一个非常长的队员名字用于验证截断', avatarRef: null, teamRole: '负责跨端协作与基础设施维护的长期职责描述', status: 'available' },
  { agentId: 'agent_a', displayName: '甲', avatarRef: null, teamRole: '后端工程师', status: 'available' },
  { agentId: 'agent_c', displayName: '丙', avatarRef: null, teamRole: '前端工程师', status: 'away' }
]

function render(viewMembers: TeamPresetMemberView[], unavailable = false): string {
  return renderToStaticMarkup(createElement(TeamPresetMemberSummary, { preset, members: viewMembers, unavailable }))
}

// Static-markup evidence for the read-only tile grid: settings-style tiles,
// lead shown first, no member or lead editing controls.
describe('TeamPresetMemberSummary', () => {
  it('renders one read-only tile per member with name, role and the lead badge', () => {
    const markup = render(members)
    expect(markup).toContain('new-camp-preset-member')
    expect(markup).toContain('new-camp-preset-member-copy')
    expect(markup).toContain('new-camp-preset-lead-badge')
    expect(markup).toContain('甲')
    expect(markup).toContain('后端工程师')
    expect(markup).toContain('暂不在场')
    expect(markup).toContain('将按此队伍创建')
  })

  it('shows the lead tile first even when it is not first in the source order', () => {
    const markup = render(members)
    expect(markup.indexOf('甲')).toBeLessThan(markup.indexOf('一个非常长的队员名字用于验证截断'))
    expect(markup.indexOf('甲')).toBeLessThan(markup.indexOf('new-camp-preset-lead-badge') + 200)
  })

  it('contains no editing controls, search or management entry', () => {
    const markup = render(members)
    expect(markup).not.toContain('<input')
    expect(markup).not.toContain('role="radio"')
    expect(markup).not.toContain('设为队长')
    expect(markup).not.toContain('管理队员')
    expect(markup).not.toContain('移除')
  })

  it('shows the correction alert only when a member is unavailable', () => {
    expect(render(members)).not.toContain('compact-inline-error')
    expect(render(members, true)).toContain('compact-inline-error')
    expect(render(members, true)).toContain('队伍」页修正')
  })

  it('keeps a long name and long role inside the tile copy for ellipsis', () => {
    const markup = render(members)
    expect(markup).toContain('一个非常长的队员名字用于验证截断')
    expect(markup).toContain('负责跨端协作与基础设施维护的长期职责描述')
  })

  it('shows the full introduction above the grid and omits it when empty', () => {
    const withDescription = renderToStaticMarkup(createElement(TeamPresetMemberSummary, { preset, members, unavailable: false, description: '负责服务端接口、数据访问与并发错误路径。\n第二行也保留。' }))
    expect(withDescription).toContain('new-camp-preset-description')
    expect(withDescription).toContain('负责服务端接口、数据访问与并发错误路径。')
    expect(withDescription).toContain('第二行也保留。')
    expect(withDescription.indexOf('new-camp-preset-description')).toBeLessThan(withDescription.indexOf('new-camp-preset-members'))
    expect(render(members)).not.toContain('new-camp-preset-description')
  })
})
