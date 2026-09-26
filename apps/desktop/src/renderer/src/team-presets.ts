import type { AgentProfile, TeamPreset } from '@contracts'

/**
 * A Team Preset member is usable for Camp creation only when the AgentProfile is
 * still present. Save-time Core admission allows a present-but-away member; Camp
 * creation rejects it with `team_preset_members_unavailable`, so the workspace
 * must surface that state instead of silently dropping the member.
 */
export type TeamPresetMemberStatus = 'available' | 'away' | 'removed' | 'missing'

export interface TeamPresetMemberView {
  agentId: string
  displayName: string
  avatarRef: string | null
  teamRole: string
  status: TeamPresetMemberStatus
}

export const TEAM_PRESET_NAME_MAX = 40
export const TEAM_PRESET_DESCRIPTION_MAX = 200
export const TEAM_PRESET_MEMBER_MAX = 100
export const TEAM_PRESET_LIMIT = 32

export function normalizeTeamPresetName(value: string): string {
  return value.trim().replace(/\s+/gu, ' ')
}

/** Trims the edges but preserves the introduction's internal line breaks. */
export function normalizeTeamPresetDescription(value: string): string {
  return value.trim()
}

export function presentTeamMembers(agents: readonly AgentProfile[]): AgentProfile[] {
  return agents.filter((agent) => agent.removedAt === null && agent.presence === 'present')
}

export function teamPresetMemberViews(
  preset: Pick<TeamPreset, 'memberAgentIds'>,
  agents: readonly AgentProfile[]
): TeamPresetMemberView[] {
  const byId = new Map(agents.map((agent) => [agent.agentId, agent]))
  return preset.memberAgentIds.map((agentId) => {
    const profile = byId.get(agentId)
    if (!profile) {
      return { agentId, displayName: agentId, avatarRef: null, teamRole: '', status: 'missing' }
    }
    const status: TeamPresetMemberStatus = profile.removedAt !== null || profile.presence === 'removed'
      ? 'removed'
      : profile.presence === 'away' ? 'away' : 'available'
    return {
      agentId,
      displayName: profile.displayName,
      avatarRef: profile.avatarRef,
      teamRole: profile.teamRole,
      status
    }
  })
}

export function teamPresetHasUnavailableMembers(views: readonly TeamPresetMemberView[]): boolean {
  return views.some((view) => view.status !== 'available')
}

/**
 * Presentation order: the lead first, then the remaining members in their
 * original order. This is a view projection only; the caller's stored order is
 * never rewritten.
 */
export function leadFirstMemberViews(
  views: readonly TeamPresetMemberView[],
  leadAgentId: string
): TeamPresetMemberView[] {
  const lead = views.find((view) => view.agentId === leadAgentId)
  return lead ? [lead, ...views.filter((view) => view.agentId !== lead.agentId)] : [...views]
}

/**
 * Presentation order for a Team Preset card: the lead first, then the remaining
 * members in their persisted order. This is a view projection only; the stored
 * `memberAgentIds` order is never rewritten.
 */
export function teamPresetMembersLeadFirst(
  preset: Pick<TeamPreset, 'memberAgentIds' | 'leadAgentId'>,
  agents: readonly AgentProfile[]
): TeamPresetMemberView[] {
  return leadFirstMemberViews(teamPresetMemberViews(preset, agents), preset.leadAgentId)
}

export function teamPresetMemberStatusLabel(status: TeamPresetMemberStatus): string {
  switch (status) {
    case 'away': return '暂不在场'
    case 'removed': return '已移除'
    case 'missing': return '已不存在'
    default: return '可用'
  }
}

export interface TeamPresetLeadOption {
  agentId: string
  displayName: string
  avatarRef: string | null
  teamRole: string
  selectable: boolean
  statusLabel: string
}

/**
 * The lead candidates for a Team Preset editor. They come only from the members
 * currently selected in the draft, never the full Agent list, so a non-member
 * can never become the lead. A removed/away member stays visible with a status
 * but is not selectable; the current lead is therefore never silently replaced.
 */
export function teamPresetLeadOptions(
  memberAgentIds: readonly string[],
  agents: readonly AgentProfile[]
): TeamPresetLeadOption[] {
  return teamPresetMemberViews({ memberAgentIds: [...memberAgentIds] }, agents).map((member) => ({
    agentId: member.agentId,
    displayName: member.displayName,
    avatarRef: member.avatarRef,
    teamRole: member.teamRole,
    selectable: member.status === 'available',
    statusLabel: teamPresetMemberStatusLabel(member.status)
  }))
}

/**
 * Toggles a member in the editor draft. Removing the current lead falls back to
 * the first remaining member; toggling any other member never rewrites the
 * lead, so an away lead is preserved until the user explicitly changes it.
 */
export function toggleTeamPresetMember(input: {
  memberAgentIds: string[]
  leadAgentId: string
  toggledMemberId: string
}): { memberAgentIds: string[]; leadAgentId: string } {
  const memberAgentIds = input.memberAgentIds.includes(input.toggledMemberId)
    ? input.memberAgentIds.filter((id) => id !== input.toggledMemberId)
    : [...input.memberAgentIds, input.toggledMemberId]
  const leadAgentId = memberAgentIds.includes(input.leadAgentId)
    ? input.leadAgentId
    : memberAgentIds[0] ?? ''
  return { memberAgentIds, leadAgentId }
}

export interface TeamPresetDraft {
  name: string
  description?: string
  memberAgentIds: string[]
  leadAgentId: string
}

export function teamPresetDraftError(draft: TeamPresetDraft): string | null {
  const name = normalizeTeamPresetName(draft.name)
  if (!name) return '请填写队伍名称。'
  if (Array.from(name).length > TEAM_PRESET_NAME_MAX) return `队伍名称最多 ${TEAM_PRESET_NAME_MAX} 个字符。`
  const description = normalizeTeamPresetDescription(draft.description ?? '')
  if (Array.from(description).length > TEAM_PRESET_DESCRIPTION_MAX) return `队伍介绍最多 ${TEAM_PRESET_DESCRIPTION_MAX} 个字符。`
  if (draft.memberAgentIds.length === 0) return '请至少选择一位队员。'
  if (draft.memberAgentIds.length > TEAM_PRESET_MEMBER_MAX) return `队伍最多 ${TEAM_PRESET_MEMBER_MAX} 位队员。`
  if (new Set(draft.memberAgentIds).size !== draft.memberAgentIds.length) return '队员不能重复。'
  if (!draft.leadAgentId) return '请选择队长。'
  if (!draft.memberAgentIds.includes(draft.leadAgentId)) return '队长必须是队伍成员。'
  return null
}

/** Reads the typed Core error code; message text is never matched. */
export function readTeamPresetErrorCode(error: unknown): string | null {
  if (typeof error !== 'object' || error === null || !('code' in error)) return null
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : null
}

export function teamPresetErrorMessage(code: string | null, fallback: string): string {
  switch (code) {
    case 'team_preset_not_found': return '这支队伍已不存在，可能已被删除。'
    case 'team_preset_revision_conflict': return '队伍已在别处更新，已重新读取最新内容，请确认后重试。'
    case 'team_preset_name_taken': return '已有同名队伍，请换一个名称。'
    case 'team_preset_limit_reached': return `最多只能保存 ${TEAM_PRESET_LIMIT} 支队伍，请先删除不再使用的队伍。`
    case 'team_preset_members_unavailable': return '队伍包含已移除或不在场的队员，请到队伍页修正后再使用。'
    default: return fallback
  }
}
