import pytest
from finpipe.providers.groq import GroqAdapter
from finpipe.providers.llm_base import LlmProviderBase


def test_llm_provider_base_is_mixin_for_all_llm_adapters():
    assert issubclass(GroqAdapter, LlmProviderBase)


@pytest.mark.asyncio
async def test_llm_provider_prepare_prompt_uses_shared_config(config):
    adapter = GroqAdapter(config)
    result = await adapter.prepare_prompt("<b>NVDA</b> rally")
    assert result == "NVDA rally"
