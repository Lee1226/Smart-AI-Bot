import { useQuery } from '@tanstack/react-query'
import { fetchEnabledLLMModels } from '../lib/api'

export function useExecutionModels() {
  const query = useQuery({
    queryKey: ['execution-models'],
    queryFn: fetchEnabledLLMModels,
  })
  return { models: query.data ?? [], isLoading: query.isLoading, isError: query.isError }
}

export function ExecutionModelSelect({
  value, onChange, disabled = false,
}: {
  value: string
  onChange: (modelId: string) => void
  disabled?: boolean
}) {
  const { models, isLoading, isError } = useExecutionModels()
  const selected = models.find(model => model.id === value)
  return (
    <div>
      <label className="mb-2 block text-sm font-semibold text-slate-700">执行模型</label>
      <select
        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-4 focus:ring-blue-500/10"
        value={value}
        disabled={disabled || isLoading || isError || models.length === 0}
        onChange={event => onChange(event.target.value)}
      >
        <option value="">{isLoading ? '正在加载模型…' : isError ? '模型列表加载失败' : '请选择模型'}</option>
        {models.map(model => (
          <option key={model.id} value={model.id}>
            {model.connection_name || '未命名连接'} · {model.provider || 'provider'} · {model.model_name}
          </option>
        ))}
      </select>
      {models.length === 0 && !isLoading && !isError && (
        <p className="mt-1.5 text-xs text-amber-600">模型仓库中没有已启用的模型，请先添加并启用模型。</p>
      )}
      {selected && (
        <p className="mt-1.5 text-xs text-slate-400">
          {selected.description || selected.capabilities?.join(' / ') || selected.model_name}
        </p>
      )}
    </div>
  )
}
