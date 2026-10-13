// 单一事实来源：所有页面的 Provider / Model 配置都从这里引用。
// 加新 provider 只需改这一个文件。

export const PROVIDER_MODELS: Record<string, string[]> = {
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
  ark: ['your-endpoint-id'],
  bedrock: ['us.anthropic.claude-sonnet-4-6', 'us.anthropic.claude-opus-4-8', 'us.anthropic.claude-haiku-4-5-20251001-v1:0'],
  google: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash'],
  zhipuai: ['glm-4v', 'glm-4v-plus', 'glm-4-plus', 'glm-4-flash'],
  groq: ['llama-3.1-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
  ollama: ['llama3', 'llama3.1', 'qwen2', 'mistral'],
}

export const PROVIDERS = Object.keys(PROVIDER_MODELS)

// 快速任务 / 套件编辑的默认模型（每个 provider 取列表第一项）
export const DEFAULT_MODEL: Record<string, string> = Object.fromEntries(
  Object.entries(PROVIDER_MODELS).map(([k, v]) => [k, v[0]])
)
