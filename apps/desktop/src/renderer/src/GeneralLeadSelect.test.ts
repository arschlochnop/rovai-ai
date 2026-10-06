import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { AgentProfile } from '@contracts'
import { GENERAL_LEAD_SELECT_LABELS, GeneralLeadSelect } from './GeneralLeadSelect'

function agent(agentId: string, displayName: string): AgentProfile {
  return {
    agentId,
    displayName,
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
    removedAt: null
  }
}

// T7 S-1: extracting the shared combobox must not change any settings copy or
// ARIA label. The popover/search labels are pinned as data because Radix portals
// are not rendered by the static server renderer.
describe('GeneralLeadSelect settings contract', () => {
  const agents = [agent('agent_0', '队员0'), agent('agent_1', '队员1')]

  it('keeps the exact settings labels and aria after extraction', () => {
    expect(GENERAL_LEAD_SELECT_LABELS).toEqual({
      ariaLabel: '默认队长',
      popoverAriaLabel: '选择默认队长',
      listAriaLabel: '默认队长候选',
      placeholder: '请选择队长',
      valueHint: '从默认队员中选择',
      searchAriaLabel: '搜索队长',
      searchPlaceholder: '搜索已选队员',
      emptyMessage: '没有匹配的队员'
    })
  })

  it('renders the settings trigger with its original aria label and value hint', () => {
    const markup = renderToStaticMarkup(createElement(GeneralLeadSelect, {
      agents,
      value: '',
      disabled: false,
      onChange: () => undefined
    }))
    expect(markup).toContain('role="combobox"')
    expect(markup).toContain('aria-label="默认队长"')
    expect(markup).toContain('请选择队长')
    expect(markup).toContain('从默认队员中选择')
  })

  it('keeps a selected lead visible with its role', () => {
    const markup = renderToStaticMarkup(createElement(GeneralLeadSelect, {
      agents,
      value: 'agent_0',
      disabled: false,
      onChange: () => undefined
    }))
    expect(markup).toContain('队员0')
    expect(markup).toContain('队员')
  })
})
