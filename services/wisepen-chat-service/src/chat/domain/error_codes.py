from common.core.domain import IErrorCode


class ChatErrorCode(IErrorCode):
    # --- 会话相关 ---
    SESSION_NOT_FOUND = (40001, "目标会话不存在")
    CONTEXT_LIMIT_EXCEEDED = (40002, "对话上下文超出模型限制")

    # --- 模型相关 ---
    LLM_GENERATION_FAILED = (50011, "大模型生成失败")

    # --- 记忆相关 ---
    MEMORY_NOT_FOUND = (40001, "目标记忆不存在")
    MEMORY_OPERATION_FAILED = (50021, "记忆操作失败")

    # --- 用户自定义搜索源相关 ---
    CUSTOM_PROVIDER_NOT_CONFIGURED = (40101, "自定义搜索源未配置")
    CUSTOM_PROVIDER_INVALID_MODE = (40102, "搜索模式不合法")
    CUSTOM_PROVIDER_INVALID_PROVIDER = (40103, "自定义搜索源服务商不合法")
    CUSTOM_PROVIDER_KEY_ALREADY_EXISTS = (40104, "自定义搜索源密钥已存在")
    CUSTOM_PROVIDER_KEY_INVALID = (40105, "自定义搜索源 API Key 无效")
    CUSTOM_PROVIDER_QUOTA_EXHAUSTED = (40106, "自定义搜索源额度已用尽")
    CUSTOM_PROVIDER_RATE_LIMITED = (40107, "自定义搜索源请求被限流")
    CUSTOM_PROVIDER_TIMEOUT = (40108, "自定义搜索源请求超时")
    CUSTOM_PROVIDER_ERROR = (50101, "自定义搜索源不可用")
    CUSTOM_PROVIDER_EMPTY_RESULT = (40109, "自定义搜索源没有返回可用结果")
