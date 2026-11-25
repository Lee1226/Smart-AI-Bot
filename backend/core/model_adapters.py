"""Protocol adapters implemented by the model connection layer."""

SUPPORTED_MODEL_ADAPTERS = frozenset({
    "openai",
    "anthropic",
    "google",
    "dashscope",
    "ark",
    "zhipuai",
    "groq",
    "ollama",
    "bedrock",
    "openai-compatible",
})
