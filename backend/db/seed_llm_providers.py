"""Initial provider catalog data.

These rows are inserted into each account database once. Runtime UI options are
always read from llm_providers; this module is seed data, not a provider lookup.
"""
DEFAULT_LLM_PROVIDERS = [
    {"provider_id": "openai", "name": "OpenAI", "protocol_type": "native", "adapter_id": "openai", "default_base_url": "https://api.openai.com/v1", "auth_type": "api_key", "model_presets_json": '["gpt-4o","gpt-4o-mini","gpt-4-turbo","gpt-3.5-turbo"]'},
    {"provider_id": "anthropic", "name": "Anthropic", "protocol_type": "native", "adapter_id": "anthropic", "default_base_url": "", "auth_type": "api_key", "model_presets_json": '["claude-opus-4-6","claude-sonnet-4-6","claude-haiku-4-5"]'},
    {"provider_id": "google", "name": "Google Gemini", "protocol_type": "native", "adapter_id": "google", "default_base_url": "", "auth_type": "api_key", "model_presets_json": '["gemini-1.5-pro","gemini-1.5-flash","gemini-2.0-flash"]'},
    {"provider_id": "dashscope", "name": "阿里云百炼 / DashScope", "protocol_type": "openai-compatible", "adapter_id": "dashscope", "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "auth_type": "api_key", "model_presets_json": '["qwen-plus"]'},
    {"provider_id": "ark", "name": "火山引擎 Ark", "protocol_type": "openai-compatible", "adapter_id": "ark", "default_base_url": "https://ark.cn-beijing.volces.com/api/v3", "auth_type": "api_key", "model_presets_json": "[]"},
    {"provider_id": "zhipuai", "name": "智谱 AI", "protocol_type": "openai-compatible", "adapter_id": "zhipuai", "default_base_url": "https://open.bigmodel.cn/api/paas/v4", "auth_type": "api_key", "model_presets_json": '["glm-4-plus","glm-4-flash"]'},
    {"provider_id": "groq", "name": "Groq", "protocol_type": "openai-compatible", "adapter_id": "groq", "default_base_url": "https://api.groq.com/openai/v1", "auth_type": "api_key", "model_presets_json": '["llama-3.1-70b-versatile","llama-3.1-8b-instant"]'},
    {"provider_id": "ollama", "name": "Ollama", "protocol_type": "openai-compatible", "adapter_id": "ollama", "default_base_url": "http://localhost:11434", "auth_type": "none", "model_presets_json": '["llama3","llama3.1","qwen2","mistral"]'},
    {"provider_id": "bedrock", "name": "AWS Bedrock", "protocol_type": "native", "adapter_id": "bedrock", "default_base_url": "", "auth_type": "aws", "model_presets_json": '["us.anthropic.claude-sonnet-4-6","us.anthropic.claude-opus-4-8"]'},
    {"provider_id": "openai-compatible", "name": "OpenAI Compatible", "protocol_type": "openai-compatible", "adapter_id": "openai-compatible", "default_base_url": "", "auth_type": "api_key", "model_presets_json": "[]"},
]
