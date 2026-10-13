export function parseUiXml(xml: string) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml')
  if (doc.querySelector('parsererror') || !doc.querySelector('hierarchy > node')) throw new Error('无法读取 UI 树')
  let focused: Record<string, unknown> | undefined
  let packageName = ''
  const node = (el: Element): Record<string, unknown> => {
    const a = (key: string) => el.getAttribute(key) || ''
    const bounds = a('bounds').match(/^\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]$/)
    if (!bounds) throw new Error('UI 节点缺少有效坐标')
    const [, left, top, right, bottom] = bounds.map(Number)
    const result: Record<string, unknown> = {
      className: a('class'), text: a('text'), contentDescription: a('content-desc'), resourceId: a('resource-id'),
      boundsInScreen: { left, top, right, bottom }, isVisibleToUser: right > left && bottom > top,
      isEnabled: a('enabled') === 'true', isClickable: a('clickable') === 'true',
      isFocusable: a('focusable') === 'true', isCheckable: a('checkable') === 'true',
      isChecked: a('checked') === 'true', isScrollable: a('scrollable') === 'true', isSelected: a('selected') === 'true',
      children: Array.from(el.children).filter(c => c.tagName === 'node').map(node),
    }
    if (!packageName && a('package')) packageName = a('package')
    if (a('focused') === 'true') focused = result
    return result
  }
  const children = Array.from(doc.documentElement.children).filter(c => c.tagName === 'node').map(node)
  const rotation = Number(doc.documentElement.getAttribute('rotation') || '0')
  return { rotation, a11y_tree: { className: 'hierarchy', isVisibleToUser: true, children },
    phone_state: { packageName, currentApp: packageName,
      isEditable: String(focused?.className || '').includes('EditText'), focusedElement: focused } }
}
