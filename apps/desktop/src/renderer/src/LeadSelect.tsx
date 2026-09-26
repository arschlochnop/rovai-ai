import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { DialogControlIcon } from './AppDialog'
import { MemberAvatar } from './MemberAvatar'

/**
 * One candidate for the shared, accessible lead combobox. `selectable` is false
 * for a member that is present but no longer usable (removed/away); it stays
 * visible with `statusLabel` so the current lead is never silently replaced.
 */
export interface LeadSelectOption {
  agentId: string
  displayName: string
  avatarRef: string | null
  teamRole: string
  selectable: boolean
  statusLabel?: string
}

/**
 * Shared lead combobox used by the settings default team and the Team Preset
 * editor. The caller supplies the exact candidate set; this component never
 * widens it. It keeps the established `general-lead-*` presentation classes so
 * both surfaces share one visual and keyboard contract.
 */
export function LeadSelect({
  options,
  value,
  disabled,
  onChange,
  ariaLabel,
  listAriaLabel,
  popoverAriaLabel,
  placeholder = '请选择队长',
  valueHint = '',
  searchAriaLabel,
  searchPlaceholder = '搜索队员',
  emptyMessage = '没有匹配的队员',
  searchThreshold = 8,
  portalContainer
}: {
  options: LeadSelectOption[]
  value: string
  disabled: boolean
  onChange(agentId: string): void
  ariaLabel: string
  listAriaLabel: string
  /** Distinct from the listbox label; the settings surface uses its own copy. */
  popoverAriaLabel?: string
  placeholder?: string
  valueHint?: string
  searchAriaLabel?: string
  searchPlaceholder?: string
  emptyMessage?: string
  searchThreshold?: number
  /**
   * Mounts the popup inside the owning Dialog content. Without it, a popup in a
   * modal is portaled to `document.body` and the parent Dialog treats clicks on
   * the options as an outside interaction, closing the editor.
   */
  portalContainer?: HTMLElement | null
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeId, setActiveId] = useState(value)
  const keyboardOpened = useRef(false)
  const search = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const id = useId()
  const selected = options.find(option => option.agentId === value)
  const filtered = options.filter(option => `${option.displayName} ${option.teamRole}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const enabled = filtered.filter(option => option.selectable)
  const active = enabled.find(option => option.agentId === activeId) ?? enabled[0]
  const activeIndex = filtered.findIndex(option => option.agentId === active?.agentId)
  const searchable = options.length > searchThreshold
  const activeDescendant = open && activeIndex >= 0 ? `${id}-${activeIndex}` : undefined

  function changeOpen(next: boolean): void {
    if (disabled && next) return
    if (next) {
      setQuery('')
      setActiveId(value)
    }
    setOpen(next)
  }

  function choose(option: LeadSelectOption): void {
    if (disabled || !option.selectable) return
    onChange(option.agentId)
    setOpen(false)
  }

  function handleKey(event: KeyboardEvent<HTMLElement>): void {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
    const navigationKey = ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)
    const selectKey = event.key === 'Enter' || (event.key === ' ' && !(event.target instanceof HTMLInputElement))
    if (!navigationKey && !selectKey) return
    if (disabled) return
    keyboardOpened.current = true
    event.preventDefault()
    if (!open) {
      keyboardOpened.current = true
      changeOpen(true)
      return
    }
    if (selectKey) {
      if (active) choose(active)
      return
    }
    const currentIndex = enabled.findIndex(option => option.agentId === active?.agentId)
    const nextIndex = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1
      : Math.max(0, Math.min(enabled.length - 1, currentIndex + (event.key === 'ArrowDown' ? 1 : -1)))
    if (enabled[nextIndex]) setActiveId(enabled[nextIndex].agentId)
  }

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  useEffect(() => {
    if (open && activeIndex >= 0) {
      const container = list.current
      const option = container?.querySelector(`[data-option-index="${activeIndex}"]`)
      if (!container || !option) return
      // Scroll this list only; moving ancestor scroll areas would move the popup anchor.
      const bounds = container.getBoundingClientRect()
      const row = option.getBoundingClientRect()
      if (row.top < bounds.top) container.scrollTop -= bounds.top - row.top
      else if (row.bottom > bounds.bottom) container.scrollTop += row.bottom - bounds.bottom
    }
  }, [open, activeIndex])

  const selectedSecondary = selected
    ? selected.selectable ? selected.teamRole : selected.statusLabel ?? '已失效'
    : valueHint

  return (
    <Popover.Root open={open} onOpenChange={changeOpen}>
      <Popover.Trigger asChild>
        <button type="button" className="general-lead-trigger" role="combobox" aria-label={ariaLabel}
          aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="listbox"
          aria-activedescendant={activeDescendant} disabled={disabled}
          onPointerDown={() => { keyboardOpened.current = false }} onKeyDown={handleKey}>
          {selected && <MemberAvatar agentId={selected.agentId} avatarRef={selected.avatarRef} displayName={selected.displayName} size="mention" decorative />}
          <span className="general-lead-value"><strong>{selected?.displayName ?? placeholder}</strong><small>{selectedSecondary}</small></span>
          <DialogControlIcon name="chevron" />
        </button>
      </Popover.Trigger>
      <Popover.Portal container={portalContainer ?? undefined}>
        <Popover.Content className="general-lead-popover" align="end" sideOffset={6} collisionPadding={12}
          collisionBoundary={portalContainer ?? undefined}
          aria-label={popoverAriaLabel ?? listAriaLabel} onKeyDown={handleKey}
          onOpenAutoFocus={event => {
            event.preventDefault()
            if (keyboardOpened.current) (searchable ? search.current : list.current)?.focus()
          }}>
          {searchable && <div className="general-lead-search">
            <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
            <input ref={search} type="search" aria-label={searchAriaLabel ?? searchPlaceholder} placeholder={searchPlaceholder}
              aria-controls={id} aria-activedescendant={activeDescendant} autoComplete="off" value={query}
              onChange={event => { setQuery(event.target.value); setActiveId('') }} />
          </div>}
          <div ref={list} id={id} role="listbox" aria-label={listAriaLabel} aria-activedescendant={activeDescendant} tabIndex={-1} className="general-lead-options">
            {filtered.map((option, index) => <div id={`${id}-${index}`} data-option-index={index} key={option.agentId}
              role="option" aria-selected={option.agentId === value} aria-disabled={!option.selectable}
              className={`general-lead-option${option.agentId === active?.agentId ? ' is-active' : ''}`}
              onClick={() => choose(option)} title={option.displayName}>
              <MemberAvatar agentId={option.agentId} avatarRef={option.avatarRef} displayName={option.displayName} size="mention" decorative />
              <span className="general-lead-option-copy"><strong>{option.displayName}</strong><small>{option.selectable ? option.teamRole : option.statusLabel ?? '已失效'}</small></span>
              {option.agentId === value && <DialogControlIcon name="check" />}
            </div>)}
            {!filtered.length && <p className="general-lead-empty">{emptyMessage}</p>}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
