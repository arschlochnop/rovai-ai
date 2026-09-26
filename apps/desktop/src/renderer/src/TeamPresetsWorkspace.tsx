import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import type { AgentProfile, TeamPreset, TeamPresetsSnapshot } from '@contracts'
import { useCampClient } from './camp-client'
import { readErrorMessage } from './error-message'
import { MemberAvatar } from './MemberAvatar'
import { LeadSelect, type LeadSelectOption } from './LeadSelect'
import {
  AppDialogBody,
  AppDialogContent,
  AppDialogFooter,
  AppDialogHeader
} from './AppDialog'
import {
  TEAM_PRESET_DESCRIPTION_MAX,
  TEAM_PRESET_LIMIT,
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
  toggleTeamPresetMember,
  type TeamPresetDraft,
  type TeamPresetMemberView
} from './team-presets'
import './team-presets.css'

type EditorState = { key: number; preset: TeamPreset | null }

/**
 * Named Team Presets workspace. It reads and writes only
 * `preferences.teamPresets.*`; the shared default-team editor and one-click
 * creation stay untouched. A preset is a reusable creation input, never a live
 * Camp roster.
 */
export function TeamPresetsWorkspace({
  agents,
  topNotices,
  onNotify
}: {
  agents: AgentProfile[]
  topNotices?: React.ReactNode
  onNotify?(message: string): void
}): React.JSX.Element {
  const client = useCampClient()
  const generation = useRef(0)
  const editorSequence = useRef(0)
  const [snapshot, setSnapshot] = useState<TeamPresetsSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [editor, setEditor] = useState<EditorState | null>(null)
  const [editorError, setEditorError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<TeamPreset | null>(null)
  const [deletingBusy, setDeletingBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const presets = snapshot?.presets ?? []

  const fetchPresets = useCallback(async (): Promise<TeamPresetsSnapshot | null> => {
    try {
      const next = await client.request<TeamPresetsSnapshot>('preferences.teamPresets.list', {})
      setSnapshot(next)
      setLoadError(null)
      return next
    } catch (error) {
      setLoadError(readErrorMessage(error))
      return null
    }
  }, [client])

  const load = useCallback(async (): Promise<void> => {
    const request = ++generation.current
    setLoading(true)
    try {
      await fetchPresets()
    } finally {
      if (request === generation.current) setLoading(false)
    }
  }, [fetchPresets])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    // Desktop delivers the typed change event; the browser adapter only exposes
    // a coarse invalidation, so the workspace re-reads on either signal.
    const unsubscribes = [
      client.onEvent?.((event) => {
        if (event.method === 'preferences.team_presets_changed') void load()
      }),
      client.onInvalidated?.(() => { void load() })
    ].filter((unsubscribe): unsubscribe is () => void => typeof unsubscribe === 'function')
    return () => { for (const unsubscribe of unsubscribes) unsubscribe() }
  }, [client, load])

  const openCreate = (): void => {
    setEditorError(null)
    setActionError(null)
    setFeedback(null)
    setEditor({ key: ++editorSequence.current, preset: null })
  }

  const openEdit = (preset: TeamPreset): void => {
    setEditorError(null)
    setActionError(null)
    setFeedback(null)
    setEditor({ key: ++editorSequence.current, preset })
  }

  const submitEditor = async (draft: TeamPresetDraft): Promise<void> => {
    if (!editor) return
    const draftError = teamPresetDraftError(draft)
    if (draftError) {
      setEditorError(draftError)
      return
    }
    setSaving(true)
    setEditorError(null)
    try {
      const next = await client.request<TeamPresetsSnapshot>('preferences.teamPresets.save', {
        preset: {
          id: editor.preset?.id ?? null,
          name: normalizeTeamPresetName(draft.name),
          description: normalizeTeamPresetDescription(draft.description ?? ''),
          memberAgentIds: draft.memberAgentIds,
          leadAgentId: draft.leadAgentId
        },
        expectedRevision: editor.preset?.revision ?? null
      })
      setSnapshot(next)
      setEditor(null)
      setFeedback(editor.preset ? '队伍已保存。' : '队伍已新建。')
    } catch (error) {
      const code = readTeamPresetErrorCode(error)
      if (code === 'team_preset_revision_conflict') {
        const refreshed = await fetchPresets()
        const editingId = editor.preset?.id
        const found = editingId ? refreshed?.presets.find((preset) => preset.id === editingId) : undefined
        if (found) setEditor((current) => (current ? { ...current, preset: found } : current))
        setEditorError(teamPresetErrorMessage(code, readErrorMessage(error)))
      } else if (code === 'team_preset_not_found') {
        setEditor(null)
        setActionError(teamPresetErrorMessage(code, readErrorMessage(error)))
        void load()
      } else {
        setEditorError(teamPresetErrorMessage(code, readErrorMessage(error)))
      }
    } finally {
      setSaving(false)
    }
  }

  const confirmDelete = async (): Promise<void> => {
    if (!deleting) return
    setDeletingBusy(true)
    setDeleteError(null)
    try {
      const next = await client.request<TeamPresetsSnapshot>('preferences.teamPresets.delete', {
        id: deleting.id,
        expectedRevision: deleting.revision
      })
      setSnapshot(next)
      setDeleting(null)
      setFeedback('队伍已删除。')
      onNotify?.('队伍已删除')
    } catch (error) {
      const code = readTeamPresetErrorCode(error)
      if (code === 'team_preset_revision_conflict') {
        const refreshed = await fetchPresets()
        const found = refreshed?.presets.find((preset) => preset.id === deleting.id)
        if (found) setDeleting(found)
        setDeleteError(teamPresetErrorMessage(code, readErrorMessage(error)))
      } else if (code === 'team_preset_not_found') {
        setDeleting(null)
        void load()
        setActionError(teamPresetErrorMessage(code, readErrorMessage(error)))
      } else {
        setDeleteError(teamPresetErrorMessage(code, readErrorMessage(error)))
      }
    } finally {
      setDeletingBusy(false)
    }
  }

  return (
    <section className="teams-workspace" aria-labelledby="teams-workspace-title" aria-busy={loading}>
      <header className="teams-header">
        <div>
          <h2 id="teams-workspace-title">队伍</h2>
          <p>保存可复用的队员与队长组合，在新建对话时直接选择。</p>
        </div>
        <div className="teams-header-actions">
          <button
            className="primary-button"
            type="button"
            onClick={openCreate}
            disabled={loading || presets.length >= TEAM_PRESET_LIMIT}
          >
            新建队伍
          </button>
        </div>
      </header>

      {topNotices && <div className="teams-page-notices">{topNotices}</div>}

      {loadError && (
        <div className="teams-error" role="alert">
          <strong>队伍读取失败</strong>
          <span>{loadError}</span>
          <button className="quiet-button compact" type="button" onClick={() => void load()}>重试</button>
        </div>
      )}
      {actionError && (
        <div className="teams-error" role="alert">
          <strong>操作未完成</strong>
          <span>{actionError}</span>
          <button className="quiet-button compact" type="button" onClick={() => setActionError(null)}>知道了</button>
        </div>
      )}
      {feedback && <div className="teams-feedback" role="status">{feedback}</div>}

      {loading && !snapshot ? (
        <p className="teams-empty" role="status">正在读取队伍…</p>
      ) : presets.length === 0 ? (
        <div className="teams-empty">
          <strong>还没有保存的队伍</strong>
          <p>把常用的队员与队长组合保存为队伍，新建对话时即可一键选择。</p>
          <button className="primary-button" type="button" onClick={openCreate}>新建队伍</button>
        </div>
      ) : (
        <ul className="teams-list">
          {presets.map((preset) => (
            <TeamPresetCard
              key={preset.id}
              preset={preset}
              agents={agents}
              onEdit={openEdit}
              onDelete={(target) => { setDeleteError(null); setFeedback(null); setDeleting(target) }}
            />
          ))}
        </ul>
      )}

      {editor && (
        <TeamPresetEditor
          key={editor.key}
          open
          preset={editor.preset}
          agents={agents}
          busy={saving}
          error={editorError}
          onOpenChange={(open) => { if (!open && !saving) setEditor(null) }}
          onSave={submitEditor}
        />
      )}

      <Dialog.Root open={deleting !== null} onOpenChange={(open) => { if (!open && !deletingBusy) setDeleting(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <AppDialogContent width="compact" onEscapeKeyDown={(event) => { if (deletingBusy) event.preventDefault() }}>
            <AppDialogHeader
              title="删除队伍"
              description="只会删除这支可复用的队伍，不影响已创建的对话，也不影响设置里的默认队员。"
              descriptionId="team-delete-description"
              closeDisabled={deletingBusy}
            />
            <AppDialogBody>
              <p className="team-delete-body">确定删除「{deleting?.name}」？此操作无法撤销。</p>
              {deleteError && <p className="compact-inline-error" role="alert">{deleteError}</p>}
            </AppDialogBody>
            <AppDialogFooter>
              <Dialog.Close asChild>
                <button className="compact-cancel" type="button" disabled={deletingBusy}>取消</button>
              </Dialog.Close>
              <button className="primary-button is-danger" type="button" disabled={deletingBusy} onClick={() => void confirmDelete()}>
                {deletingBusy ? '正在删除…' : '删除'}
              </button>
            </AppDialogFooter>
          </AppDialogContent>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  )
}

/** Presentational card, exported so the empty/valid/unavailable states can be
 * verified without mounting Core-backed effects. */
export function TeamPresetCard({
  preset,
  agents,
  onEdit,
  onDelete
}: {
  preset: TeamPreset
  agents: AgentProfile[]
  onEdit(preset: TeamPreset): void
  onDelete(preset: TeamPreset): void
}): React.JSX.Element {
  const members = teamPresetMembersLeadFirst(preset, agents)
  const unavailable = teamPresetHasUnavailableMembers(members)
  const profileById = new Map(agents.map((agent) => [agent.agentId, agent]))
  return (
    <li className="team-card">
      <div className="team-card-main">
        <div className="team-card-title">
          <strong>{preset.name}</strong>
          <span>{preset.memberAgentIds.length} 位队员</span>
        </div>
        {preset.description && <p className="team-card-description">{preset.description}</p>}
        <ul className="team-card-members" aria-label={`${preset.name} 的队员`}>
          {members.map((member) => (
            <li
              key={member.agentId}
              className={`team-card-member${member.status === 'available' ? '' : ' is-unavailable'}`}
            >
              <MemberAvatar
                agentId={member.agentId}
                avatarRef={profileById.get(member.agentId)?.avatarRef ?? member.avatarRef}
                displayName={member.displayName}
                size="mention"
                decorative
              />
              <span className="team-card-member-copy">
                <strong>{member.displayName}</strong>
                <small>{member.status === 'available' ? member.teamRole || '队员' : teamPresetMemberStatusLabel(member.status)}</small>
              </span>
              {preset.leadAgentId === member.agentId && <b className="team-card-lead-badge">队长</b>}
            </li>
          ))}
        </ul>
        {unavailable && (
          <p className="team-card-warning" role="status">
            包含已移除或不在场的队员，创建对话时会被 Core 拒绝。请编辑修正后再使用。
          </p>
        )}
      </div>
      <div className="team-card-actions">
        <button className="quiet-button compact" type="button" onClick={() => onEdit(preset)}>编辑</button>
        <button className="quiet-button compact is-danger" type="button" onClick={() => onDelete(preset)}>删除</button>
      </div>
    </li>
  )
}

function TeamPresetEditor({
  open,
  preset,
  agents,
  busy,
  error,
  onOpenChange,
  onSave
}: {
  open: boolean
  preset: TeamPreset | null
  agents: AgentProfile[]
  busy: boolean
  error: string | null
  onOpenChange(open: boolean): void
  onSave(draft: TeamPresetDraft): Promise<void>
}): React.JSX.Element {
  const [name, setName] = useState(preset?.name ?? '')
  const [description, setDescription] = useState(preset?.description ?? '')
  const [selectedIds, setSelectedIds] = useState<string[]>(preset?.memberAgentIds ?? [])
  const [leadId, setLeadId] = useState(preset?.leadAgentId ?? '')
  const [draftError, setDraftError] = useState<string | null>(null)
  const [dialogContent, setDialogContent] = useState<HTMLDivElement | null>(null)

  const candidates = useMemo(() => presentTeamMembers(agents), [agents])
  const candidateIds = useMemo(() => new Set(candidates.map((agent) => agent.agentId)), [candidates])
  const unavailable: TeamPresetMemberView[] = useMemo(
    () => teamPresetMemberViews({ memberAgentIds: selectedIds.filter((id) => !candidateIds.has(id)) }, agents),
    [agents, candidateIds, selectedIds]
  )
  const leadOptions: LeadSelectOption[] = useMemo(
    () => teamPresetLeadOptions(selectedIds, agents),
    [agents, selectedIds]
  )

  const toggleMember = (agentId: string): void => {
    setDraftError(null)
    const next = toggleTeamPresetMember({ memberAgentIds: selectedIds, leadAgentId: leadId, toggledMemberId: agentId })
    setSelectedIds(next.memberAgentIds)
    setLeadId(next.leadAgentId)
  }

  const submit = (event: React.FormEvent): void => {
    event.preventDefault()
    const message = teamPresetDraftError({ name, description, memberAgentIds: selectedIds, leadAgentId: leadId })
    if (message) {
      setDraftError(message)
      return
    }
    void onSave({ name, description, memberAgentIds: selectedIds, leadAgentId: leadId })
  }

  const nameLength = Array.from(normalizeTeamPresetName(name)).length
  const descriptionLength = Array.from(normalizeTeamPresetDescription(description)).length
  const visibleError = draftError ?? error

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next) }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <AppDialogContent
          ref={setDialogContent}
          width="wide"
          className="team-preset-editor-dialog"
          onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }}
          onInteractOutside={(event) => {
            // The lead combobox popup is portaled into this Dialog content once
            // mounted, so Radix does not treat option clicks as outside. This
            // guard also covers the first paint before the container ref exists,
            // when Radix would otherwise portal the popup to document.body and
            // the Dialog would close on the first option click.
            const target = (event.detail as { originalEvent?: Event } | undefined)?.originalEvent?.target
            if (target instanceof Element && target.closest('.general-lead-popover')) event.preventDefault()
          }}
        >
          <form onSubmit={submit}>
            <AppDialogHeader
              title={preset ? '编辑队伍' : '新建队伍'}
              description="填写名称与介绍，选择至少一位队员，并指定其中一位队长。"
              descriptionId="team-editor-description"
              closeDisabled={busy}
            />
            <AppDialogBody>
              <label className="team-editor-field">
                <span>队伍名称</span>
                <input
                  value={name}
                  disabled={busy}
                  aria-invalid={Boolean(visibleError && nameLength === 0)}
                  maxLength={TEAM_PRESET_NAME_MAX}
                  placeholder="例如：后端开发"
                  onChange={(event) => { setDraftError(null); setName(event.target.value) }}
                />
                <small>{nameLength} / {TEAM_PRESET_NAME_MAX}</small>
              </label>

              <label className="team-editor-field">
                <span>队伍介绍<span className="team-editor-optional">可选</span></span>
                <textarea
                  value={description}
                  disabled={busy}
                  rows={3}
                  maxLength={TEAM_PRESET_DESCRIPTION_MAX}
                  placeholder="记录这支队伍的职责与适用场景"
                  onChange={(event) => { setDraftError(null); setDescription(event.target.value) }}
                />
                <small>{descriptionLength} / {TEAM_PRESET_DESCRIPTION_MAX}</small>
              </label>

              <fieldset className="team-editor-fieldset">
                <legend>队员<span>{selectedIds.length} 位已选</span></legend>
                {candidates.length === 0 && <p className="team-editor-empty" role="status">暂无在场队员，请先在「队员」中配置。</p>}
                <ul className="team-editor-member-list">
                  {candidates.map((agent) => (
                    <li key={agent.agentId}>
                      <label>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(agent.agentId)}
                          disabled={busy}
                          onChange={() => toggleMember(agent.agentId)}
                        />
                        <MemberAvatar agentId={agent.agentId} avatarRef={agent.avatarRef} displayName={agent.displayName} size="mention" decorative />
                        <span><strong>{agent.displayName}</strong><small>{agent.teamRole}</small></span>
                      </label>
                    </li>
                  ))}
                </ul>
                {unavailable.length > 0 && (
                  <div className="team-editor-unavailable">
                    <p role="status">以下队员已移除或不在场，保存后创建对话会被拒绝。请移除或替换。</p>
                    <ul>
                      {unavailable.map((member) => (
                        <li key={member.agentId}>
                          <span>{member.displayName}<em>{teamPresetMemberStatusLabel(member.status)}</em></span>
                          <button className="quiet-button compact" type="button" disabled={busy} onClick={() => toggleMember(member.agentId)}>移除</button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </fieldset>

              <fieldset className="team-editor-fieldset">
                <legend>队长</legend>
                {selectedIds.length === 0
                  ? <p className="team-editor-empty" role="status">请先选择队员。</p>
                  : (
                    <LeadSelect
                      options={leadOptions}
                      value={leadId}
                      disabled={busy}
                      onChange={(agentId) => { setDraftError(null); setLeadId(agentId) }}
                      ariaLabel="队伍队长"
                      popoverAriaLabel="选择队伍队长"
                      listAriaLabel="队伍队长候选"
                      placeholder="选择队长"
                      valueHint="从已选队员中选择"
                      searchAriaLabel="搜索已选队员"
                      searchPlaceholder="搜索已选队员"
                      emptyMessage="没有匹配的队员"
                      portalContainer={dialogContent}
                    />
                  )}
              </fieldset>

              {visibleError && <p className="compact-inline-error" role="alert">{visibleError}</p>}
            </AppDialogBody>
            <AppDialogFooter>
              <Dialog.Close asChild>
                <button className="compact-cancel" type="button" disabled={busy}>取消</button>
              </Dialog.Close>
              <button className="primary-button" type="submit" disabled={busy}>{busy ? '正在保存…' : '保存'}</button>
            </AppDialogFooter>
          </form>
        </AppDialogContent>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
