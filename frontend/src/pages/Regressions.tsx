import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import TestAccountSelect from '../components/TestAccountSelect'
import { api, fetchDevices, fetchSuites } from '../lib/api'

type Target = {problems?:{status:string;reason:string}[];run_id:string;suite_name:string;device_name:string;status:string;passed:number;total:number;failed:number;errored:number;skipped:number;preparation?:Record<string, unknown>;diagnostics?:{status?:string;reason?:string}}
type Plan = {id:string;name:string;gate:string;config:Record<string,unknown>;targets:Target[]}
type Artifact = {id:string;filename:string;version_label:string;sha256:string}
const labels:Record<string,string> = {queued:'排队中',preparing:'准备中',running:'运行中',pending:'待运行',done:'已结束',error:'执行错误',cancelled:'已取消',interrupted:'服务中断',waiting:'等待结果',passed:'通过 · 允许发布',failed:'未通过 · 阻止发布'}
const box = 'border rounded-md px-3 py-2 bg-white text-sm w-full'
export default function Regressions() {
  const devices=useQuery({queryKey:['devices'],queryFn:fetchDevices,refetchInterval:3000})
  const suites=useQuery({queryKey:['regression-suites'],queryFn:()=>fetchSuites({page_size:100})})
  const plans=useQuery({queryKey:['regressions'],queryFn:()=>api.get<Plan[]>('/regressions').then(r=>r.data),refetchInterval:3000})
  const artifacts=useQuery({queryKey:['regression-artifacts'],queryFn:()=>api.get<Artifact[]>('/regressions/artifacts').then(r=>r.data)})
  const [selected,setSelected]=useState(''),[name,setName]=useState(''),[environment,setEnvironment]=useState('默认环境')
  const [testAccounts,setTestAccounts]=useState<Record<string,string>>({})
  const [suiteIds,setSuiteIds]=useState<string[]>([]),[deviceIds,setDeviceIds]=useState<string[]>([])
  const [artifactId,setArtifactId]=useState(''),[version,setVersion]=useState(''),[pkg,setPkg]=useState(''),[clear,setClear]=useState(false)
  const [steps,setSteps]=useState(20),[commit,setCommit]=useState(''),[build,setBuild]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const plan=plans.data?.find(p=>p.id===selected) || plans.data?.[0]
  const toggle=(ids:string[],id:string,set:(v:string[])=>void)=>set(ids.includes(id)?ids.filter(x=>x!==id):[...ids,id])
  const act=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();await plans.refetch()}catch(e:any){setError(e.response?.data?.detail||e.message)}finally{setBusy(false)}}
  const download=async(kind:string)=>{if(!plan)return;await act(async()=>{const r=await api.get(`/regressions/${plan.id}/${kind}`,{responseType:'blob'});const url=URL.createObjectURL(r.data);const a=document.createElement('a');a.href=url;a.download=`regression-${plan.id}-${kind}`;a.click();URL.revokeObjectURL(url)})}
  return <div className="space-y-6">
    <div><h1 className="text-2xl font-semibold">多设备回归</h1><p className="text-sm text-ink-mute mt-2">同一设备按任务排队，不同设备并发执行。每个套件与设备组合生成独立运行记录。</p></div>
    {error&&<div role="alert" className="bg-red-50 text-red-700 p-3 rounded">{error}</div>}
    <form className="border rounded-xl bg-white p-5 space-y-4" onSubmit={e=>{e.preventDefault();void act(async()=>{const r=await api.post<Plan>('/regressions',{name,suite_ids:suiteIds,device_ids:deviceIds,test_accounts:testAccounts,environment,artifact_id:artifactId,app_package:pkg,clear_data:clear,max_steps:steps,commit_sha:commit,build_url:build});setSelected(r.data.id)})}}>
      <h2 className="font-semibold">创建回归计划</h2>
      <div className="grid grid-cols-2 gap-4"><label className="text-sm">计划名称<input required aria-label="计划名称" className={box} value={name} onChange={e=>setName(e.target.value)}/></label><label className="text-sm">环境标签<input aria-label="环境标签" className={box} value={environment} onChange={e=>setEnvironment(e.target.value)}/></label></div>
      <p className="text-xs text-ink-mute">环境标签用于追溯；准备步骤支持安装 APK、清应用数据和启动应用。不会自动切换服务端环境。</p>
      <div className="grid grid-cols-2 gap-4">
        <fieldset className="border rounded p-3 max-h-48 overflow-auto"><legend className="text-sm px-1">用例套件</legend>{suites.data?.items.filter(s=>s.case_count>0).map(s=><label key={s.id} className="flex gap-2 text-sm py-1"><input type="checkbox" checked={suiteIds.includes(s.id)} onChange={()=>toggle(suiteIds,s.id,setSuiteIds)}/>{s.name}（{s.case_count}）</label>)}</fieldset>
        <fieldset className="border rounded p-3 max-h-48 overflow-auto"><legend className="text-sm px-1">在线设备</legend>{devices.data?.filter(d=>d.status==='online').map(d=><label key={d.id} className="flex gap-2 text-sm py-1"><input type="checkbox" checked={deviceIds.includes(d.id)} onChange={()=>toggle(deviceIds,d.id,setDeviceIds)}/>{d.name} · {d.connection_type}</label>)}</fieldset>
      </div>
      {!!suiteIds.length && !!deviceIds.length && <section className="space-y-3 border rounded p-3">
        <h3 className="text-sm font-medium">测试账号 · 每个套件与设备组合单独选择</h3>
        <p className="text-xs text-ink-mute">未选择时使用项目默认账号；手机号与验证码、邮箱与密码均可自动填写，验证码过期后需更新。并发任务应使用不同账号。</p>
        {suiteIds.map(sid => deviceIds.map(did => {
          const suite = suites.data?.items.find(s => s.id === sid)
          const device = devices.data?.find(d => d.id === did)
          const key = `${sid}:${did}`
          return <div key={key} className="border rounded p-2 space-y-1">
            <p className="text-xs">{suite?.name} · {device?.name}</p>
            {suite?.project_id ? <TestAccountSelect projectId={suite.project_id} platform={device?.platform} value={testAccounts[key] || ''}
              onChange={value => setTestAccounts(previous => ({ ...previous, [key]: value }))} /> : <p className="text-xs text-gray-500">请先为套件选择所属项目。</p>}
          </div>
        }))}
      </section>}
      <details><summary className="cursor-pointer text-sm">安装包与环境准备、CI 信息（可选）</summary><div className="space-y-3 mt-3">
        <div className="grid grid-cols-2 gap-4"><label className="text-sm">APK 版本标签<input className={box} value={version} onChange={e=>setVersion(e.target.value)}/></label><label className="text-sm">上传 APK<input aria-label="上传 APK" type="file" accept=".apk" disabled={busy} className={box} onChange={e=>{const f=e.target.files?.[0];if(f)void act(async()=>{const form=new FormData();form.append('file',f);form.append('version_label',version);const r=await api.post<Artifact>('/regressions/artifacts',form);setArtifactId(r.data.id);await artifacts.refetch()})}}/></label></div>
        <label className="block text-sm">安装包<select aria-label="安装包" className={box} value={artifactId} onChange={e=>setArtifactId(e.target.value)}><option value="">使用设备已安装应用</option>{artifacts.data?.map(a=><option value={a.id} key={a.id}>{a.filename} · {a.version_label} · {a.sha256.slice(0,12)}</option>)}</select></label>
        <label className="block text-sm">被测应用包名<input aria-label="被测应用包名" className={box} placeholder="com.example.app" value={pkg} onChange={e=>setPkg(e.target.value)}/></label>
        <label className="flex gap-2 text-sm text-red-700"><input type="checkbox" checked={clear} onChange={e=>setClear(e.target.checked)}/>执行前清除该应用数据（删除登录状态和本地数据）</label>
        <p className="text-xs text-ink-mute">自动安装与清数据仅支持 Android ADB 桥接，包名需与套件应用一致。安装前校验 SHA-256 与真实 APK 包名。</p>
        <div className="grid grid-cols-3 gap-3"><label className="text-sm">每用例最大步数<input type="number" min={1} max={100} className={box} value={steps} onChange={e=>setSteps(+e.target.value)}/></label><label className="text-sm">Commit SHA<input className={box} value={commit} onChange={e=>setCommit(e.target.value)}/></label><label className="text-sm">构建链接<input className={box} value={build} onChange={e=>setBuild(e.target.value)}/></label></div>
      </div></details>
      <button disabled={busy||!suiteIds.length||!deviceIds.length} className="bg-primary text-white rounded px-4 py-2 disabled:opacity-40">创建并运行（{suiteIds.length*deviceIds.length} 个组合）</button>
    </form>
    <div className="grid grid-cols-[240px_1fr] gap-5">
      <div className="space-y-2">{plans.data?.map(p=><button key={p.id} onClick={()=>setSelected(p.id)} className={`w-full text-left p-3 border rounded ${plan?.id===p.id?'bg-primary-soft':'bg-white'}`}><div className="text-sm font-medium">{p.name}</div><div className="text-xs mt-1">{labels[p.gate]}</div></button>)}{!plans.data?.length&&<p className="text-sm text-ink-mute">暂无回归计划</p>}</div>
      {plan&&<section className="border rounded-xl bg-white p-5 min-w-0 space-y-4">
        <div className="flex justify-between gap-3"><h2 className="font-semibold">{plan.name}</h2><strong className={plan.gate==='passed'?'text-green-700':plan.gate==='failed'?'text-red-700':'text-amber-700'}>{labels[plan.gate]}</strong></div>
        <p className="text-xs text-ink-mute">环境：{String(plan.config.environment)} · Commit：{String(plan.config.commit_sha||'未绑定')} · APK：{String(plan.config.artifact_filename||'设备现有版本')}</p>
        <div className="flex gap-2 text-sm"><button disabled={busy||plan.gate!=='waiting'} className="border rounded px-3 py-1 disabled:opacity-40" onClick={()=>void act(()=>api.post(`/regressions/${plan.id}/cancel`))}>取消计划</button><button disabled={busy||plan.gate!=='failed'} className="border rounded px-3 py-1 disabled:opacity-40" onClick={()=>void act(async()=>{const r=await api.post<Plan>(`/regressions/${plan.id}/retry`);setSelected(r.data.id)})}>仅重跑失败组合</button><button className="border rounded px-3 py-1" onClick={()=>void download('junit.xml')}>下载 JUnit</button><button disabled={plan.gate==='waiting'} className="border rounded px-3 py-1 disabled:opacity-40" onClick={()=>void download('evidence.zip')}>下载证据包</button></div>
        <div className="overflow-auto"><table className="w-full text-sm"><thead><tr className="text-left border-b"><th className="py-2">套件</th><th>设备</th><th>状态</th><th>通过 / 总数</th><th>证据</th></tr></thead><tbody>{plan.targets.map(t=><tr key={t.run_id} className="border-b"><td className="py-3"><Link className="text-primary-deep" to={`/runs/${t.run_id}`}>{t.suite_name}</Link></td><td>{t.device_name}</td><td>{labels[t.status]||t.status}{t.problems?.map((p,i)=><p key={i} className="text-xs text-red-700 max-w-xs mt-1">{p.status==='skip'?'跳过':p.status==='fail'?'失败':'错误'}：{p.reason}</p>)}</td><td>{t.passed} / {t.total}{!!(t.failed+t.errored+t.skipped)&&<span className="text-red-700"> · 失败/错误/跳过 {t.failed+t.errored+t.skipped}</span>}</td><td>{t.diagnostics?.status==='collected'?'设备日志已归档':t.diagnostics?.status==='unsupported'?'步骤截图与日志':t.diagnostics?.status==='error'?'诊断采集失败':(['done','error','cancelled','interrupted'].includes(t.status)?'未采集设备诊断':'等待归档')}<details><summary className="text-xs cursor-pointer">准备详情</summary><pre className="text-xs max-w-[220px] whitespace-pre-wrap break-all">{JSON.stringify(t.preparation||{},null,2)}</pre></details></td></tr>)}</tbody></table></div>
        <p className="text-xs text-ink-mute">门禁要求所有组合、所有用例通过；取消、跳过、中断或错误均阻止发布。排队后修改套件会阻止执行，需创建新计划。</p>
      </section>}
    </div>
  </div>
}
