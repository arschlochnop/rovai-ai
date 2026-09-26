import type { AgentProfile } from '@contracts'
import { LeadSelect, type LeadSelectOption } from './LeadSelect'

function selectable(agent: AgentProfile): boolean {
  return agent.presence === 'present' && agent.removedAt === null
}

/**
 * The settings default-team selector's exact visible copy and ARIA labels. They
 * are exported so the extraction regression is pinned by a unit test instead of
 * relying on the settings page snapshot alone.
 */
export const GENERAL_LEAD_SELECT_LABELS = {
  ariaLabel: '默认队长',
  popoverAriaLabel: '选择默认队长',
  listAriaLabel: '默认队长候选',
  placeholder: '请选择队长',
  valueHint: '从默认队员中选择',
  searchAriaLabel: '搜索队长',
  searchPlaceholder: '搜索已选队员',
  emptyMessage: '没有匹配的队员'
} as const

/** Settings default-team lead picker; the shared combobox owns all behavior. */
export function GeneralLeadSelect({ agents, value, disabled, onChange }: {
  agents: AgentProfile[]
  value: string
  disabled: boolean
  onChange(agentId: string): void
}): React.JSX.Element {
  const options: LeadSelectOption[] = agents.map(agent => ({
    agentId: agent.agentId,
    displayName: agent.displayName,
    avatarRef: agent.avatarRef,
    teamRole: agent.teamRole,
    selectable: selectable(agent),
    statusLabel: '已失效'
  }))
  return (
    <LeadSelect
      options={options}
      value={value}
      disabled={disabled}
      onChange={onChange}
      {...GENERAL_LEAD_SELECT_LABELS}
    />
  )
}
