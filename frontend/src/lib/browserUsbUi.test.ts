// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { parseUiXml } from './browserUsbUi'

const fixture = `<hierarchy rotation="1"><node class="android.widget.FrameLayout" package="com.android.settings" bounds="[0,0][2400,1080]" enabled="true"><node class="android.widget.EditText" package="com.android.settings" resource-id="com.android.settings:id/search" bounds="[12,30][812,90]" text="搜索 &amp; 设置" content-desc="搜索" enabled="true" clickable="true" focusable="true" focused="true"/><node class="android.widget.Button" bounds="[10,100][110,150]" text="不可用" enabled="false" clickable="true"/><node class="android.widget.CheckBox" bounds="[10,160][110,210]" enabled="true" checkable="true" checked="true" selected="true"/></node></hierarchy>`

describe('UIAutomator to SAB perception contract', () => {
  it('preserves native bounds, decoded Chinese text, focus and enabled/checked flags', () => {
    const state = parseUiXml(fixture)
    expect(state.rotation).toBe(1)
    expect(state.phone_state.packageName).toBe('com.android.settings')
    expect(state.phone_state.isEditable).toBe(true)
    expect(state.phone_state.focusedElement?.text).toBe('搜索 & 设置')
    const root = state.a11y_tree.children[0]
    const children = root.children as Record<string, unknown>[]
    expect(children[0].boundsInScreen).toEqual({ left: 12, top: 30, right: 812, bottom: 90 })
    expect(children[0].resourceId).toBe('com.android.settings:id/search')
    expect(children[1].isEnabled).toBe(false)
    expect(children[2].isChecked).toBe(true)
    expect(children[2].isSelected).toBe(true)
  })
  it('rejects malformed, empty, or coordinate-less dumps so stale data cannot drive taps', () => {
    for (const xml of ['<hierarchy><node>', '<hierarchy/>', '<hierarchy><node class="Button"/></hierarchy>']) {
      expect(() => parseUiXml(xml)).toThrow()
    }
  })
  it('does not claim focusable containers imply an editable input', () => {
    const state = parseUiXml(fixture.replace('android.widget.EditText', 'android.widget.Button'))
    expect(state.phone_state.isEditable).toBe(false)
  })
})
