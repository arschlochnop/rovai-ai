import { describe, expect, it } from 'vitest'
import { WEB_OPERATIONS } from './client'

// The Web Host admits operations individually. Adding a Core request to the
// Renderer without adding it here would make the shared Team Presets workspace
// fail only at runtime in a browser.
describe('Web operation allowlist', () => {
  it('admits the three Team Preset preferences requests', () => {
    for (const operation of [
      'preferences.teamPresets.list',
      'preferences.teamPresets.save',
      'preferences.teamPresets.delete'
    ]) {
      expect(WEB_OPERATIONS).toContain(operation)
    }
  })

  it('does not admit a default-team or new-conversation mutation through Team Presets', () => {
    expect(WEB_OPERATIONS).not.toContain('preferences.teamPresets.setDefault')
    expect(WEB_OPERATIONS).not.toContain('preferences.newConversation.initialize')
  })
})
