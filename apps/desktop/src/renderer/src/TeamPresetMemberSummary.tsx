import type { TeamPreset } from '@contracts'
import { MemberAvatar } from './MemberAvatar'
import { leadFirstMemberViews, teamPresetMemberStatusLabel, type TeamPresetMemberView } from './team-presets'

/**
 * Read-only summary of a selected Team Preset inside the New Conversation
 * Dialog. It mirrors the settings "默认队员" tile grid: avatar + name + team
 * role, no checkbox, no search and no management entry. The lead tile is shown
 * first (view-only) and the member area scrolls internally so the Dialog never
 * grows with the member count.
 */
export function TeamPresetMemberSummary({
  preset,
  description,
  members,
  unavailable
}: {
  preset: Pick<TeamPreset, 'name' | 'leadAgentId'>
  description?: string
  members: TeamPresetMemberView[]
  unavailable: boolean
}): React.JSX.Element {
  const orderedMembers = leadFirstMemberViews(members, preset.leadAgentId)
  return (
    <div className="new-camp-preset-summary">
      {description && <p className="new-camp-preset-description">{description}</p>}
      <ul className="new-camp-preset-members" aria-label={`${preset.name} 的队员`}>
        {orderedMembers.map((member) => (
          <li
            key={member.agentId}
            className={`new-camp-preset-member${member.status === 'available' ? '' : ' is-unavailable'}`}
          >
            <MemberAvatar agentId={member.agentId} avatarRef={member.avatarRef} displayName={member.displayName} size="mention" decorative />
            <span className="new-camp-preset-member-copy">
              <strong>{member.displayName}</strong>
              <small>{member.status === 'available' ? member.teamRole || '队员' : teamPresetMemberStatusLabel(member.status)}</small>
            </span>
            {preset.leadAgentId === member.agentId && <b className="new-camp-preset-lead-badge">队长</b>}
          </li>
        ))}
      </ul>
      {unavailable && <p className="compact-inline-error" role="alert">所选队伍包含已移除或不在场的队员，请到「队伍」页修正后再创建。</p>}
      <p className="compact-inline-note">将按此队伍创建；创建后不再与原队伍关联，修改队伍不会影响已创建的对话。</p>
    </div>
  )
}
