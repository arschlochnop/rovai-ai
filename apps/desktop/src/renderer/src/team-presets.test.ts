import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { AgentProfile } from '@contracts'
import { TeamPresetCard } from './TeamPresetsWorkspace'
import {
  TEAM_PRESET_DESCRIPTION_MAX,
  TEAM_PRESET_NAME_MAX,
  normalizeTeamPresetDescription,
  normalizeTeamPresetName,
  presentTeamMembers,
  readTeamPresetErrorCode,
  teamPresetDraftError,
  teamPresetErrorMessage,
  teamPresetHasUnavailableMembers,
  teamPresetLeadOptions,
  teamPresetMemberStatusLabel,
  teamPresetMemberViews,
  teamPresetMembersLeadFirst,
  toggleTeamPresetMember
} from './team-presets'

function agent(overrides: Partial<AgentProfile> & Pick<AgentProfile, 'agentId' | 'displayName'>): AgentProfile {
  return {
    avatarRef: null,
    accent: null,
    teamRole: '队员',
    professionalResponsibilities: '',
    personalityTraits: [],
    workingPrinciples: '',
    growthTopic: '',
    defaultCapabilities: [],
    presence: 'present',
    runtimeConfiguration: null,
    runtimeReadiness: { status: 'ready', blockers: [] },
    memberOrder: 0,
    version: 1,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    removedAt: null,
    ...overrides
  }
}

describe('Team Preset member availability', () => {
  const agents = [
    agent({ agentId: 'agent_a', displayName: '甲' }),
    agent({ agentId: 'agent_b', displayName: '乙', presence: 'away' }),
    agent({ agentId: 'agent_c', displayName: '丙', presence: 'removed', removedAt: '2026-02-01T00:00:00Z' })
  ]

  it('distinguishes present, away, removed and missing members', () => {
    const views = teamPresetMemberViews({ memberAgentIds: ['agent_a', 'agent_b', 'agent_c', 'agent_gone'] }, agents)
    expect(views.map((view) => view.status)).toEqual(['available', 'away', 'removed', 'missing'])
    expect(views[3].displayName).toBe('agent_gone')
    expect(teamPresetHasUnavailableMembers(views)).toBe(true)
  })

  it('reports an all-present preset as available', () => {
    const views = teamPresetMemberViews({ memberAgentIds: ['agent_a'] }, agents)
    expect(teamPresetHasUnavailableMembers(views)).toBe(false)
  })

  it('offers only present members as editor candidates', () => {
    expect(presentTeamMembers(agents).map((member) => member.agentId)).toEqual(['agent_a'])
  })

  it('labels every availability state', () => {
    expect(teamPresetMemberStatusLabel('available')).toBe('可用')
    expect(teamPresetMemberStatusLabel('away')).toBe('暂不在场')
    expect(teamPresetMemberStatusLabel('removed')).toBe('已移除')
    expect(teamPresetMemberStatusLabel('missing')).toBe('已不存在')
  })
})

describe('Team Preset lead candidates', () => {
  const agents = [
    agent({ agentId: 'agent_a', displayName: '甲' }),
    agent({ agentId: 'agent_b', displayName: '乙', presence: 'away' }),
    agent({ agentId: 'agent_c', displayName: '丙' })
  ]

  it('offers only the currently selected members, never the full Agent list', () => {
    const options = teamPresetLeadOptions(['agent_a', 'agent_b'], agents)
    expect(options.map((option) => option.agentId)).toEqual(['agent_a', 'agent_b'])
    expect(options.some((option) => option.agentId === 'agent_c')).toBe(false)
  })

  it('marks away and missing members as not selectable while keeping them visible', () => {
    const options = teamPresetLeadOptions(['agent_a', 'agent_b', 'agent_gone'], agents)
    expect(options.find((option) => option.agentId === 'agent_a')).toMatchObject({ selectable: true, statusLabel: '可用' })
    expect(options.find((option) => option.agentId === 'agent_b')).toMatchObject({ selectable: false, statusLabel: '暂不在场' })
    expect(options.find((option) => option.agentId === 'agent_gone')).toMatchObject({ selectable: false, statusLabel: '已不存在', displayName: 'agent_gone' })
  })
})

describe('Team Preset member toggling', () => {
  it('selects the first remaining member only when the current lead is removed', () => {
    expect(toggleTeamPresetMember({ memberAgentIds: ['a', 'b', 'c'], leadAgentId: 'a', toggledMemberId: 'a' }))
      .toEqual({ memberAgentIds: ['b', 'c'], leadAgentId: 'b' })
  })

  it('preserves the current lead when another member is toggled, including an away lead', () => {
    expect(toggleTeamPresetMember({ memberAgentIds: ['a', 'b'], leadAgentId: 'a', toggledMemberId: 'b' }))
      .toEqual({ memberAgentIds: ['a'], leadAgentId: 'a' })
    expect(toggleTeamPresetMember({ memberAgentIds: ['a'], leadAgentId: 'a', toggledMemberId: 'c' }))
      .toEqual({ memberAgentIds: ['a', 'c'], leadAgentId: 'a' })
  })

  it('selects the newly added member when the draft had no lead', () => {
    expect(toggleTeamPresetMember({ memberAgentIds: [], leadAgentId: '', toggledMemberId: 'a' }))
      .toEqual({ memberAgentIds: ['a'], leadAgentId: 'a' })
  })
})

describe('Team Preset draft validation', () => {
  it('requires a normalized name, members and a lead inside the members', () => {
    expect(teamPresetDraftError({ name: '', memberAgentIds: ['a'], leadAgentId: 'a' })).toContain('名称')
    expect(teamPresetDraftError({ name: 'x'.repeat(TEAM_PRESET_NAME_MAX + 1), memberAgentIds: ['a'], leadAgentId: 'a' })).toContain('最多')
    expect(teamPresetDraftError({ name: '后端', memberAgentIds: [], leadAgentId: '' })).toContain('至少')
    expect(teamPresetDraftError({ name: '后端', memberAgentIds: ['a', 'b'], leadAgentId: '' })).toContain('队长')
    expect(teamPresetDraftError({ name: '后端', memberAgentIds: ['a', 'b'], leadAgentId: 'c' })).toContain('成员')
    expect(teamPresetDraftError({ name: '后端', memberAgentIds: ['a', 'a'], leadAgentId: 'a' })).toContain('重复')
    expect(teamPresetDraftError({ name: '后端', memberAgentIds: ['a', 'b'], leadAgentId: 'a' })).toBeNull()
  })

  it('normalizes whitespace before comparing length', () => {
    expect(normalizeTeamPresetName('  后端   开发  ')).toBe('后端 开发')
    expect(teamPresetDraftError({ name: '  后端   开发  ', memberAgentIds: ['a'], leadAgentId: 'a' })).toBeNull()
  })

  it('accepts an optional introduction up to 200 Unicode characters and preserves line breaks', () => {
    expect(teamPresetDraftError({ name: '后端', description: '', memberAgentIds: ['a'], leadAgentId: 'a' })).toBeNull()
    expect(normalizeTeamPresetDescription('  负责接口\n与数据  ')).toBe('负责接口\n与数据')
    expect(teamPresetDraftError({ name: '后端', description: 'x'.repeat(TEAM_PRESET_DESCRIPTION_MAX), memberAgentIds: ['a'], leadAgentId: 'a' })).toBeNull()
    expect(teamPresetDraftError({ name: '后端', description: 'x'.repeat(TEAM_PRESET_DESCRIPTION_MAX + 1), memberAgentIds: ['a'], leadAgentId: 'a' })).toContain('介绍')
  })
})

describe('Team Preset typed error handling', () => {
  it('reads the Core error code without matching the message', () => {
    expect(readTeamPresetErrorCode({ code: 'team_preset_revision_conflict', message: '队伍已在别处更新' }))
      .toBe('team_preset_revision_conflict')
    expect(readTeamPresetErrorCode(new Error('team_preset_revision_conflict'))).toBeNull()
    expect(readTeamPresetErrorCode(null)).toBeNull()
  })

  it('maps every Team Preset code to recovery copy', () => {
    expect(teamPresetErrorMessage('team_preset_not_found', 'fallback')).toContain('已不存在')
    expect(teamPresetErrorMessage('team_preset_revision_conflict', 'fallback')).toContain('重新读取')
    expect(teamPresetErrorMessage('team_preset_name_taken', 'fallback')).toContain('同名')
    expect(teamPresetErrorMessage('team_preset_limit_reached', 'fallback')).toContain('32')
    expect(teamPresetErrorMessage('team_preset_members_unavailable', 'fallback')).toContain('队伍页修正')
    expect(teamPresetErrorMessage('something_else', 'fallback')).toBe('fallback')
    expect(teamPresetErrorMessage(null, 'fallback')).toBe('fallback')
  })
})

describe('Team Preset card rendering', () => {
  const agents = [
    agent({ agentId: 'agent_a', displayName: '甲' }),
    agent({ agentId: 'agent_b', displayName: '乙', presence: 'away' })
  ]
  const render = (preset: Parameters<typeof TeamPresetCard>[0]['preset']): string =>
    renderToStaticMarkup(createElement(TeamPresetCard, { preset, agents, onEdit: () => undefined, onDelete: () => undefined }))

  it('renders a valid preset with its explicit lead', () => {
    const markup = render({ id: 'tp_1', name: '后端开发', description: '', memberAgentIds: ['agent_a'], leadAgentId: 'agent_a', revision: 1 })
    expect(markup).toContain('后端开发')
    expect(markup).toContain('甲')
    expect(markup).toContain('team-card-lead-badge')
    expect(markup).not.toContain('team-card-warning')
    expect(markup).toContain('编辑')
    expect(markup).toContain('删除')
  })

  it('shows the introduction under the name and omits the empty placeholder', () => {
    const withDescription = render({ id: 'tp_3', name: '后端开发', description: '负责服务端接口与数据访问。', memberAgentIds: ['agent_a'], leadAgentId: 'agent_a', revision: 1 })
    expect(withDescription).toContain('team-card-description')
    expect(withDescription).toContain('负责服务端接口与数据访问。')
    const withoutDescription = render({ id: 'tp_4', name: '后端开发', description: '', memberAgentIds: ['agent_a'], leadAgentId: 'agent_a', revision: 1 })
    expect(withoutDescription).not.toContain('team-card-description')
  })

  it('marks removed or away members and warns before creation', () => {
    const markup = render({ id: 'tp_2', name: '临时队', description: '', memberAgentIds: ['agent_a', 'agent_b', 'agent_gone'], leadAgentId: 'agent_b', revision: 3 })
    expect(markup).toContain('暂不在场')
    expect(markup).toContain('已不存在')
    expect(markup).toContain('team-card-warning')
    expect(markup).toContain('3 位队员')
  })

  it('shows the lead first and each member as a read-only avatar + name + team role tile', () => {
    const preset = { id: 'tp_5', name: '后端开发', description: '', memberAgentIds: ['agent_b', 'agent_a'], leadAgentId: 'agent_a', revision: 1 }
    const markup = render(preset)
    expect(markup).toContain('team-card-member-copy')
    expect(markup).toContain('team-card-lead-badge')
    expect(markup).not.toContain('<input')
    // Lead (甲 / agent_a) is rendered before the non-lead (乙 / agent_b) even
    // though the persisted memberAgentIds order is ['agent_b', 'agent_a'].
    expect(markup.indexOf('甲')).toBeLessThan(markup.indexOf('乙'))
    expect(preset.memberAgentIds).toEqual(['agent_b', 'agent_a'])
  })
})

describe('Team Preset member ordering', () => {
  const agents = [
    agent({ agentId: 'agent_a', displayName: '甲', teamRole: '后端工程师' }),
    agent({ agentId: 'agent_b', displayName: '乙', teamRole: '前端工程师' })
  ]

  it('projects the lead first without mutating the persisted member order', () => {
    const preset = { memberAgentIds: ['agent_b', 'agent_a'], leadAgentId: 'agent_a' }
    expect(teamPresetMembersLeadFirst(preset, agents).map((view) => view.agentId)).toEqual(['agent_a', 'agent_b'])
    expect(preset.memberAgentIds).toEqual(['agent_b', 'agent_a'])
  })

  it('keeps the persisted order when there is no matching lead', () => {
    const preset = { memberAgentIds: ['agent_b', 'agent_a'], leadAgentId: 'agent_missing' }
    expect(teamPresetMembersLeadFirst(preset, agents).map((view) => view.agentId)).toEqual(['agent_b', 'agent_a'])
  })
})
