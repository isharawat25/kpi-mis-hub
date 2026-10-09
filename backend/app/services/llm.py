"""LangChain chat model factory. Returns None when no provider is configured (rule-based fallback)."""
from ..config import get_settings


def get_llm():
    s = get_settings()
    if s.llm_provider == "openai" and s.openai_api_key:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=s.openai_model, api_key=s.openai_api_key, temperature=0.1, timeout=60)
    if s.llm_provider == "azure" and s.azure_openai_api_key and s.azure_openai_endpoint:
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(azure_endpoint=s.azure_openai_endpoint, api_key=s.azure_openai_api_key,
                               azure_deployment=s.azure_openai_deployment,
                               api_version=s.azure_openai_api_version, temperature=0.1, timeout=60)
    return None


def model_name() -> str:
    s = get_settings()
    if get_llm() is None:
        return "rule-based"
    return s.openai_model if s.llm_provider == "openai" else f"azure:{s.azure_openai_deployment}"
