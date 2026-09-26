import { describe, expect, it } from 'vitest'
import {
  campCreationSubmissionBlocked,
  campCreationSubmitError,
  campCreationTeamInput
} from './new-conversation-team-mode'

describe('New Conversation team mode input', () => {
  const custom = { memberAgentIds: ['agent_a', 'agent_b'], defaultLeadAgentId: 'agent_a' }
  const preset = { id: 'tp_fixture', expectedRevision: 7 }

  it('sends the historical custom wire shape in custom mode', () => {
    const input = campCreationTeamInput('custom', custom, preset)
    expect(input).toEqual({ memberAgentIds: ['agent_a', 'agent_b'], defaultLeadAgentId: 'agent_a' })
    expect(input && 'teamPresetSelection' in input).toBe(false)
  })

  it('sends only the selection in preset mode', () => {
    const input = campCreationTeamInput('preset', custom, preset)
    expect(input).toEqual({ teamPresetSelection: { id: 'tp_fixture', expectedRevision: 7 } })
    expect(input && 'memberAgentIds' in input).toBe(false)
    expect(input && 'defaultLeadAgentId' in input).toBe(false)
  })

  it('never falls back to the custom draft when preset mode has no selection', () => {
    expect(campCreationTeamInput('preset', custom, null)).toBeNull()
  })

  it('requires a selected, fully available preset and never converts to custom', () => {
    expect(campCreationSubmitError({ mode: 'preset', customError: null, presetSelected: false, presetUnavailable: false }))
      .toContain('请选择')
    expect(campCreationSubmitError({ mode: 'preset', customError: null, presetSelected: true, presetUnavailable: true }))
      .toContain('队伍页修正')
    expect(campCreationSubmitError({ mode: 'preset', customError: null, presetSelected: true, presetUnavailable: false }))
      .toBeNull()
    expect(campCreationSubmitError({ mode: 'custom', customError: '请至少选择一位队员。', presetSelected: false, presetUnavailable: true }))
      .toBe('请至少选择一位队员。')
  })
})

describe('New Conversation mode submission blocking', () => {
  const base = {
    busy: false,
    projectBlocked: false,
    nameError: false,
    customMemberBlocked: false,
    presetSelected: false,
    presetUnavailable: false
  }

  it('blocks preset mode until a fully available team is selected', () => {
    expect(campCreationSubmissionBlocked({ ...base, mode: 'preset' })).toBe(true)
    expect(campCreationSubmissionBlocked({ ...base, mode: 'preset', presetSelected: true })).toBe(false)
    expect(campCreationSubmissionBlocked({ ...base, mode: 'preset', presetSelected: true, presetUnavailable: true })).toBe(true)
  })

  it('uses only the custom member rule in custom mode', () => {
    expect(campCreationSubmissionBlocked({ ...base, mode: 'custom', customMemberBlocked: true })).toBe(true)
    expect(campCreationSubmissionBlocked({ ...base, mode: 'custom', customMemberBlocked: false })).toBe(false)
    // A stale unavailable preset must not block the custom branch.
    expect(campCreationSubmissionBlocked({ ...base, mode: 'custom', presetUnavailable: true })).toBe(false)
  })

  it('keeps busy, project and name guards authoritative in both modes', () => {
    for (const mode of ['custom', 'preset'] as const) {
      expect(campCreationSubmissionBlocked({ ...base, mode, busy: true })).toBe(true)
      expect(campCreationSubmissionBlocked({ ...base, mode, projectBlocked: true })).toBe(true)
      expect(campCreationSubmissionBlocked({ ...base, mode, nameError: true })).toBe(true)
    }
  })
})
