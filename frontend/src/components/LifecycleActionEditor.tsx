export interface LifecycleAction {
  id: string
  action: 'stop_app' | 'start_app' | 'global_action' | 'press_key' | 'wait' | string
  args: Record<string, unknown>
  enabled: boolean
  on_failure?: 'stop' | 'continue' | string
}

const ACTION_OPTIONS = [
  { value: 'stop_app', label: '强制停止应用' },
  { value: 'start_app', label: '启动应用' },
  { value: 'global_action', label: '系统导航操作' },
  { value: 'press_key', label: '按键操作' },
  { value: 'tap', label: '点击坐标' },
  { value: 'input_text', label: '输入文本' },
  { value: 'swipe', label: '滑动屏幕' },
  { value: 'scroll', label: '滚动页面' },
  { value: 'wait', label: '等待' },
]

const inputClass = 'w-full min-w-0 rounded-md border border-gray-200 bg-white px-2.5 py-2 text-sm text-gray-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100'

export default function LifecycleActionEditor({
  title,
  description,
  actions,
  onChange,
  emptyText = '尚未添加操作。此区域完全可选，不配置时不会执行任何处理。',
}: {
  title: string
  description: string
  actions: LifecycleAction[]
  onChange: (actions: LifecycleAction[]) => void
  emptyText?: string
}) {
  const add = () => onChange([...actions, {
    id: `lifecycle_${Date.now()}_${actions.length}`,
    action: 'stop_app',
    args: { package_name: '' },
    enabled: true,
    on_failure: 'stop',
  }])
  const patch = (index: number, next: Partial<LifecycleAction>) =>
    onChange(actions.map((item, i) => i === index ? { ...item, ...next, args: next.args ? { ...item.args, ...next.args } : item.args } : item))
  const move = (index: number, delta: number) => {
    const next = [...actions]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }
  const changeAction = (index: number, action: string) => {
    const args: Record<string, unknown> =
      action === 'stop_app' || action === 'start_app' ? { package_name: '' } :
      action === 'global_action' ? { action: 'home' } :
      action === 'press_key' ? { key: 'BACK' } :
      action === 'tap' ? { x: '', y: '' } :
      action === 'input_text' ? { text: '', clear: false } :
      action === 'swipe' ? { x1: '', y1: '', x2: '', y2: '', duration_ms: 500 } :
      action === 'scroll' ? { direction: 'down', distance: 'medium' } :
      action === 'wait' ? { duration_ms: 1000 } : {}
    patch(index, { action, args })
  }

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3 space-y-3">
      <div>
        <div className="text-sm font-semibold text-gray-800">{title}</div>
        <p className="mt-1 text-xs text-gray-500">{description}</p>
      </div>
      {actions.length === 0 ? (
        <div className="rounded-md border border-dashed border-gray-300 px-3 py-4 text-center text-xs text-gray-400">{emptyText}</div>
      ) : (
        <div className="space-y-2">
          {actions.map((item, index) => (
            <div key={item.id || index} className={`rounded-md border p-3 space-y-2 ${item.enabled ? 'border-gray-200 bg-white' : 'border-gray-100 bg-gray-50 opacity-60'}`}>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-gray-400">{String(index + 1).padStart(2, '0')}</span>
                <select className={inputClass + ' flex-1'} value={item.action} onChange={e => changeAction(index, e.target.value)}>
                  {ACTION_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <label className="flex items-center gap-1 text-xs text-gray-500 whitespace-nowrap">
                  <input type="checkbox" checked={item.enabled} onChange={e => patch(index, { enabled: e.target.checked })} />
                  启用
                </label>
                <button type="button" title="上移" disabled={index === 0} onClick={() => move(index, -1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↑</button>
                <button type="button" title="下移" disabled={index === actions.length - 1} onClick={() => move(index, 1)} className="rounded border px-2 py-1 text-xs disabled:opacity-30">↓</button>
                <button type="button" title="删除" onClick={() => onChange(actions.filter((_, i) => i !== index))} className="rounded border border-red-200 px-2 py-1 text-xs text-red-600">×</button>
              </div>
              {(item.action === 'stop_app' || item.action === 'start_app') && (
                <div>
                  <label className="mb-1 block text-[11px] text-gray-500">应用包名</label>
                  <input className={inputClass} value={String(item.args.package_name || item.args.package || '')} onChange={e => patch(index, { args: { package_name: e.target.value, package: e.target.value } })} placeholder="例如 com.alibaba.wow" />
                </div>
              )}
              {item.action === 'global_action' && (
                <div>
                  <label className="mb-1 block text-[11px] text-gray-500">系统操作</label>
                  <select className={inputClass} value={String(item.args.action || 'home')} onChange={e => patch(index, { args: { action: e.target.value } })}>
                    <option value="back">返回</option><option value="home">主页</option><option value="recent">最近任务</option><option value="notifications">通知栏</option>
                  </select>
                </div>
              )}
              {item.action === 'press_key' && (
                <div>
                  <label className="mb-1 block text-[11px] text-gray-500">按键</label>
                  <select className={inputClass} value={String(item.args.key || 'BACK')} onChange={e => patch(index, { args: { key: e.target.value } })}>
                    {['BACK','HOME','ENTER','DEL','TAB','SPACE','POWER'].map(key => <option key={key} value={key}>{key}</option>)}
                  </select>
                </div>
              )}
              {item.action === 'tap' && (
                <div className="grid grid-cols-2 gap-2">
                  {(['x', 'y'] as const).map(key => <label key={key} className="text-[11px] text-gray-500">设备坐标 {key.toUpperCase()}（px）
                    <input type="number" className={inputClass} value={String(item.args[key] ?? '')} onChange={e => patch(index, { args: { [key]: e.target.value === '' ? '' : Number(e.target.value) } })} />
                  </label>)}
                </div>
              )}
              {item.action === 'input_text' && (
                <div className="space-y-2">
                  <label className="block text-[11px] text-gray-500">输入内容
                    <textarea className={inputClass} rows={2} value={String(item.args.text || '')} onChange={e => patch(index, { args: { text: e.target.value } })} placeholder="输入要写入当前焦点输入框的文本" />
                  </label>
                  <label className="flex items-center gap-2 text-xs text-gray-500"><input type="checkbox" checked={!!item.args.clear} onChange={e => patch(index, { args: { clear: e.target.checked } })} />输入前清空</label>
                </div>
              )}
              {item.action === 'swipe' && (
                <div className="grid grid-cols-2 gap-2">
                  {(['x1', 'y1', 'x2', 'y2'] as const).map(key => <label key={key} className="text-[11px] text-gray-500">{key.toUpperCase()}（设备像素）
                    <input type="number" className={inputClass} value={String(item.args[key] ?? '')} onChange={e => patch(index, { args: { [key]: Number(e.target.value) } })} />
                  </label>)}
                  <label className="col-span-2 text-[11px] text-gray-500">滑动时长（毫秒）
                    <input type="number" min="1" max="10000" className={inputClass} value={String(item.args.duration_ms ?? 500)} onChange={e => patch(index, { args: { duration_ms: Math.max(1, Math.min(10000, Number(e.target.value) || 500)) } })} />
                  </label>
                </div>
              )}
              {item.action === 'scroll' && (
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-[11px] text-gray-500">方向
                    <select className={inputClass} value={String(item.args.direction || 'down')} onChange={e => patch(index, { args: { direction: e.target.value } })}>
                      <option value="down">向下</option><option value="up">向上</option><option value="left">向左</option><option value="right">向右</option>
                    </select>
                  </label>
                  <label className="text-[11px] text-gray-500">距离
                    <select className={inputClass} value={String(item.args.distance || 'medium')} onChange={e => patch(index, { args: { distance: e.target.value } })}>
                      <option value="small">短</option><option value="medium">中</option><option value="large">长</option>
                    </select>
                  </label>
                </div>
              )}
              {item.action === 'wait' && (
                <div>
                  <label className="mb-1 block text-[11px] text-gray-500">等待时长（毫秒）</label>
                  <input type="number" min="0" max="60000" className={inputClass} value={String(item.args.duration_ms ?? 1000)} onChange={e => patch(index, { args: { duration_ms: Math.max(0, Math.min(60000, Number(e.target.value) || 0)) } })} />
                </div>
              )}
              <div className="flex items-center gap-2">
                <label className="text-[11px] text-gray-500">失败时</label>
                <select className="rounded border border-gray-200 bg-white px-2 py-1 text-xs" value={item.on_failure || 'stop'} onChange={e => patch(index, { on_failure: e.target.value })}>
                  <option value="stop">停止后续处理</option>
                  <option value="continue">继续执行后续处理</option>
                </select>
              </div>
            </div>
          ))}
        </div>
      )}
      <button type="button" onClick={add} className="w-full rounded-md border border-dashed border-blue-300 bg-blue-50/40 px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-50">＋ 添加操作</button>
    </section>
  )
}
