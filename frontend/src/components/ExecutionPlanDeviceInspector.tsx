import { useEffect, useMemo, useState } from 'react'
import { api, Device, getToken } from '../lib/api'
import H264Canvas from './H264Canvas'
import { websocketH264Source } from '../lib/h264Stream'

interface UiElement {
  index: number; text: string; className: string; resourceId: string; cx: number; cy: number
  contentDescription?: string
  bounds?: { left: number; top: number; right: number; bottom: number } | null
  clickable?: boolean; tap_candidate?: boolean; enabled?: boolean; visible?: boolean; editable?: boolean
  tap_target?: { text?: string; resourceId?: string; className?: string; bounds?: UiElement['bounds'] } | null
  parentPath?: string[]
}
interface Snapshot { screenshot_b64: string; ui_text: string; elements: UiElement[]; screen_width?: number; screen_height?: number }
type Locator = Record<string, unknown>

export default function ExecutionPlanDeviceInspector({ device, onApplyLocator, onAddAction }: {
  device: Device | null
  onApplyLocator: (locator: Locator) => void
  onAddAction: (action: string, args: Record<string, unknown>, locator?: Locator) => void
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [videoAvailable, setVideoAvailable] = useState(false)
  const [videoFailed, setVideoFailed] = useState(false)
  const [frameSize, setFrameSize] = useState<{width:number;height:number}|null>(null)
  const [selected, setSelected] = useState<UiElement | null>(null)
  const [mode, setMode] = useState<'select'|'operate'>('select')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [inputText, setInputText] = useState('')

  const source = useMemo(() => {
    if (!device?.id) return undefined
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    return websocketH264Source(`${protocol}//${location.host}/v1/devices/${device.id}/live?source=device${token ? `&token=${encodeURIComponent(token)}` : ''}`)
  }, [device?.id])

  const refresh = async () => {
    if (!device?.id) return
    setBusy(true); setError('')
    try {
      const {data} = await api.get<Snapshot>(`/recorder/snapshot?device_id=${encodeURIComponent(device.id)}`)
      setSnapshot(data)
      setSelected(current => current ? data.elements.find(e => e.index === current.index) || null : null)
    } catch (e: any) { setError(e?.response?.data?.detail || e?.message || '读取 UI Tree 失败') }
    finally { setBusy(false) }
  }

  useEffect(() => {
    setSnapshot(null); setSelected(null); setVideoFailed(false); setVideoAvailable(false); setError('')
    if (!device?.id) return
    let alive = true
    api.get<{video_available?:boolean}>(`/devices/${device.id}/capabilities`).then(({data}) => {if(alive)setVideoAvailable(!!data.video_available)}).catch(()=>{if(alive)setVideoAvailable(false)})
    void refresh()
    return () => {alive=false}
  }, [device?.id])

  useEffect(() => {
    if (!device?.id || (videoAvailable && !videoFailed)) return
    const timer = window.setInterval(() => api.get<Snapshot>(`/recorder/snapshot?device_id=${encodeURIComponent(device.id)}`).then(({data})=>setSnapshot(data)).catch(()=>undefined), 1600)
    return () => window.clearInterval(timer)
  }, [device?.id, videoAvailable, videoFailed])

  const pointFromEvent = (event: React.PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const width = snapshot?.screen_width || frameSize?.width || 0, height = snapshot?.screen_height || frameSize?.height || 0
    if (!width || !height || !rect.width || !rect.height) return null
    const frame = videoAvailable && !videoFailed && frameSize?.width && frameSize?.height ? frameSize : {width,height}
    const scale = Math.min(rect.width/frame.width,rect.height/frame.height)
    const rw=frame.width*scale,rh=frame.height*scale
    const x=event.clientX-rect.left-(rect.width-rw)/2,y=event.clientY-rect.top-(rect.height-rh)/2
    if(x<0||y<0||x>rw||y>rh)return null
    return {x:Math.round(x/scale*width/frame.width),y:Math.round(y/scale*height/frame.height)}
  }

  const act = async (action:string,args:Record<string,unknown>) => {
    if(!device?.id)return
    setBusy(true);setError('')
    try { await api.post('/recorder/action',{device_id:device.id,action,args}); await refresh() }
    catch(e:any){setError(e?.response?.data?.detail||e?.message||'设备操作失败')}
    finally{setBusy(false)}
  }

  const effective = selected?.tap_target || selected
  const locator: Locator = effective ? {
    ...(effective.resourceId?{resource_id:effective.resourceId}:{}),
    ...(effective.text?{text:effective.text}:{}),
    ...(selected?.contentDescription?{content_description:selected.contentDescription}:{}),
    ...(effective.className?{class_name:effective.className}:{}),
    ...(effective.bounds?{bounds:effective.bounds}:{}),
    ...(selected?.parentPath?.length?{parent_path:selected.parentPath}:{}),
    ...(selected?{observed_index:selected.index}:{})
  } : {}

  if(!device)return <section className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-500">请先选择调试设备。</section>
  const supported = device.platform==='android' && device.connection_type==='adb_usb'
  return <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
      <div><h3 className="text-sm font-semibold text-gray-800">真机交互检查器</h3><p className="mt-0.5 text-xs text-gray-500">{device.name} · {device.connection_type}</p></div>
      <div className="flex items-center gap-2"><span className={`rounded-full px-2 py-1 text-[11px] ${supported?'bg-green-50 text-green-700':'bg-amber-50 text-amber-700'}`}>{supported?'Android ADB':'连接方式待验证'}</span><button onClick={()=>void refresh()} disabled={busy} className="rounded-md border px-3 py-1.5 text-xs disabled:opacity-50">{busy?'处理中…':'刷新 UI Tree'}</button></div>
    </div>
    {!supported&&<div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">当前闭环优先支持 Android 本机 ADB（adb_usb）；其他连接类型需单独验证。</div>}
    <div className="grid min-w-0 grid-cols-1 xl:grid-cols-[minmax(280px,0.9fr)_minmax(300px,1.1fr)]">
      <div className="border-b border-gray-100 p-3 xl:border-b-0 xl:border-r">
        <div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold text-gray-700">设备实时画面</span><div className="flex rounded-md bg-gray-100 p-0.5 text-[11px]"><button onClick={()=>setMode('select')} className={`rounded px-2 py-1 ${mode==='select'?'bg-white text-blue-700 shadow-sm':'text-gray-500'}`}>选择控件</button><button onClick={()=>setMode('operate')} className={`rounded px-2 py-1 ${mode==='operate'?'bg-white text-blue-700 shadow-sm':'text-gray-500'}`}>操作手机</button></div></div>
        <div onPointerUp={event=>{
          const point=pointFromEvent(event);if(!point||!snapshot)return
          if(mode==='operate'){void act('tap',point);return}
          const hit=[...snapshot.elements].reverse().find(e=>e.visible!==false&&e.bounds&&point.x>=e.bounds.left&&point.x<=e.bounds.right&&point.y>=e.bounds.top&&point.y<=e.bounds.bottom)
          setSelected(hit||null)
        }} className="relative mx-auto aspect-[9/19.5] w-full max-w-[330px] overflow-hidden rounded-lg bg-[#101820]">
          {videoAvailable&&!videoFailed&&source?<H264Canvas source={source} onVideoSize={(width,height)=>setFrameSize({width,height})} onFail={()=>setVideoFailed(true)}/>:snapshot?.screenshot_b64?<img src={`data:image/png;base64,${snapshot.screenshot_b64}`} className="absolute inset-0 h-full w-full object-contain"/>:<div className="absolute inset-0 flex items-center justify-center text-xs text-gray-400">等待设备画面…</div>}
          {selected?.bounds&&<div className="pointer-events-none absolute border-2 border-blue-500 bg-blue-400/20" style={{left:`${selected.bounds.left/(snapshot?.screen_width||1)*100}%`,top:`${selected.bounds.top/(snapshot?.screen_height||1)*100}%`,width:`${(selected.bounds.right-selected.bounds.left)/(snapshot?.screen_width||1)*100}%`,height:`${(selected.bounds.bottom-selected.bounds.top)/(snapshot?.screen_height||1)*100}%`}}/>}
        </div>
        <p className="mx-auto mt-2 max-w-[330px] text-[11px] leading-5 text-gray-500">{mode==='select'?'点击画面选择 UI Tree 控件，不会触发手机操作。':'点击画面会真实操作手机，请谨慎使用。'}</p>
        <div className="mx-auto mt-3 flex max-w-[330px] gap-2">{['back','home','recent'].map(key=><button key={key} disabled={busy} onClick={()=>void act('global_action',{action:key})} className="flex-1 rounded-md border px-2 py-2 text-xs disabled:opacity-40">{key==='back'?'返回':key==='home'?'主页':'多任务'}</button>)}</div>
      </div>
      <div className="min-w-0 p-3">
        <div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold text-gray-700">UI Tree · {snapshot?.elements.length??0} 个节点</span><span className="text-[10px] text-gray-400">{snapshot?.screen_width||0} × {snapshot?.screen_height||0}</span></div>
        <div className="max-h-[330px] space-y-1 overflow-auto rounded-lg border border-gray-100 bg-gray-50 p-2">
          {(snapshot?.elements||[]).map(item=><button key={item.index} onClick={()=>setSelected(item)} className={`block w-full rounded-md px-2 py-2 text-left ${selected?.index===item.index?'bg-blue-100 ring-1 ring-blue-300':'hover:bg-white'}`}><div className="flex items-center gap-2"><span className="text-[10px] text-gray-400">#{item.index}</span><span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-800">{item.text||item.contentDescription||item.resourceId||item.className||'未命名控件'}</span><span className={`text-[10px] ${item.clickable?'text-green-700':item.tap_candidate?'text-amber-700':'text-gray-400'}`}>{item.clickable?'可点击':item.tap_candidate?'候选':'—'}</span></div><div className="mt-1 truncate pl-7 text-[10px] text-gray-400">{item.resourceId||item.className||'无 Resource ID'}</div></button>)}
          {!snapshot?.elements.length&&<div className="py-8 text-center text-xs text-gray-400">暂无 UI Tree，请刷新。</div>}
        </div>
        {selected&&<div className="mt-3 rounded-lg border border-blue-100 bg-blue-50/50 p-3"><div className="text-xs font-semibold text-gray-800">已选控件</div><div className="mt-2 space-y-1 break-all text-[11px] text-gray-600"><div>Text：{selected.text||'—'}</div><div>Resource ID：{selected.resourceId||'—'}</div><div>Content Desc：{selected.contentDescription||'—'}</div><div>Class：{selected.className||'—'}</div><div>Bounds：{selected.bounds?JSON.stringify(selected.bounds):'—'}</div><div>状态：{selected.visible===false?'不可见':selected.enabled===false?'已禁用':selected.clickable?'系统可点击':selected.tap_candidate?'点击候选':'未标记可点击'}</div></div>
          <div className="mt-3 flex flex-wrap gap-2"><button disabled={!Object.keys(locator).length} onClick={()=>onApplyLocator(locator)} className="rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white disabled:opacity-40">应用到当前步骤</button><button disabled={!selected.visible||selected.enabled===false||!(selected.clickable||selected.tap_candidate)} onClick={()=>onAddAction('tap',{},locator)} className="rounded-md border border-blue-200 bg-white px-3 py-2 text-xs text-blue-700 disabled:opacity-40">新增点击步骤</button><button disabled={!selected.visible||selected.enabled===false||!selected.editable} onClick={()=>onAddAction('input_text',{text:''},locator)} className="rounded-md border border-gray-200 bg-white px-3 py-2 text-xs disabled:opacity-40">新增输入步骤</button></div>
          <label className="mt-3 block text-[11px] text-gray-600">输入文本并在手机上执行<div className="mt-1 flex gap-2"><input value={inputText} onChange={e=>setInputText(e.target.value)} className="min-w-0 flex-1 rounded-md border px-2 py-1.5 text-xs" placeholder="输入内容"/><button disabled={!inputText||busy} onClick={()=>void act('input_text',{text:inputText,clear:false})} className="rounded-md border bg-white px-3 py-1.5 text-xs disabled:opacity-40">发送</button></div></label>
        </div>}
        {error&&<div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
      </div>
    </div>
  </section>
}
