import type { CreateCampTeamInput } from '@contracts'

/** The New Conversation Dialog offers exactly one Camp-creation team input. */
export type CampCreationTeamMode = 'custom' | 'preset'

/**
 * Builds the mutually exclusive `camps.create` team input. The custom branch is
 * the historical wire shape; the preset branch carries only the id + revision
 * so Core re-resolves the members and lead under its own lock.
 */
export function campCreationTeamInput(
  mode: CampCreationTeamMode,
  custom: { memberAgentIds: string[]; defaultLeadAgentId: string },
  preset: { id: string; expectedRevision: number } | null
): CreateCampTeamInput | null {
  if (mode === 'preset') {
    return preset
      ? { teamPresetSelection: { id: preset.id, expectedRevision: preset.expectedRevision } }
      : null
  }
  return { memberAgentIds: custom.memberAgentIds, defaultLeadAgentId: custom.defaultLeadAgentId }
}

/**
 * The blocking error for the current mode. Preset mode never falls back to the
 * custom draft or silently drops unavailable members.
 */
export function campCreationSubmitError(input: {
  mode: CampCreationTeamMode
  customError: string | null
  presetSelected: boolean
  presetUnavailable: boolean
}): string | null {
  if (input.mode === 'preset') {
    if (!input.presetSelected) return '请选择一支队伍。'
    if (input.presetUnavailable) return '所选队伍包含已移除或不在场的队员，请到队伍页修正后再创建。'
    return null
  }
  return input.customError
}

/**
 * Disables creation for the current mode. A selected preset with any removed or
 * away member stays blocked instead of degrading to the custom draft.
 */
export function campCreationSubmissionBlocked(input: {
  busy: boolean
  projectBlocked: boolean
  nameError: boolean
  mode: CampCreationTeamMode
  customMemberBlocked: boolean
  presetSelected: boolean
  presetUnavailable: boolean
}): boolean {
  if (input.busy || input.projectBlocked || input.nameError) return true
  if (input.mode === 'preset') return !input.presetSelected || input.presetUnavailable
  return input.customMemberBlocked
}
