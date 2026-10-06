import { describe, expect, it } from 'vitest'
import { isCustomThreadTeamInput, type CreateThreadTeamInput } from './index'

// The two Thread-creation branches are a strict union. These are compile-time
// assertions (checked by `pnpm typecheck`) plus the runtime narrow used by the
// Renderer when it decides whether a draft may update the shared default team.
describe('CreateThreadRequest strict team input', () => {
  it('accepts exactly one branch at a time', () => {
    const custom: CreateThreadTeamInput = { memberAgentIds: ['agent_a'], defaultLeadAgentId: 'agent_a' }
    const preset: CreateThreadTeamInput = { teamPresetSelection: { id: 'tp_fixture', expectedRevision: 1 } }

    expect(isCustomThreadTeamInput(custom)).toBe(true)
    expect(isCustomThreadTeamInput(preset)).toBe(false)
  })

  it('rejects a mixed input at the type level', () => {
    // @ts-expect-error the custom branch must not carry a Team Preset selection
    const both: CreateThreadTeamInput = {
      memberAgentIds: ['agent_a'],
      defaultLeadAgentId: 'agent_a',
      teamPresetSelection: { id: 'tp_fixture', expectedRevision: 1 }
    }
    // @ts-expect-error the preset branch must not carry editable members or lead
    const mixed: CreateThreadTeamInput = {
      memberAgentIds: ['agent_a'],
      defaultLeadAgentId: 'agent_a',
      teamPresetSelection: { id: 'tp_fixture', expectedRevision: 1 }
    }
    expect(both).toBeDefined()
    expect(mixed).toBeDefined()
  })

  it('keeps the custom branch backward compatible', () => {
    const custom = { memberAgentIds: ['agent_a'], defaultLeadAgentId: 'agent_a' } satisfies CreateThreadTeamInput
    expect(custom.memberAgentIds).toEqual(['agent_a'])
    expect('teamPresetSelection' in custom).toBe(false)
  })
})
