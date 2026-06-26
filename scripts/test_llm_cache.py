import asyncio
import time

from finpipe.core.config import (
    FinpipeConfig,
)


async def test_llm_cache():
    # Setup mock keys and mock config
    import os

    os.environ["GROQ_API_KEY"] = "mock_key_groq"
    os.environ["GEMINI_API_KEY"] = "mock_key_gemini"

    config = FinpipeConfig()

    # Mock ResilientHttpClient so this script does not hit the network.
    import unittest.mock as mock

    from finpipe.providers.groq import GroqAdapter

    adapter = GroqAdapter(config)

    # Mock the client request
    async def mock_request(*args, **kwargs):
        class MockResponse:
            def json(self):
                return {
                    "choices": [{"message": {"content": "Hello World!"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }

        await asyncio.sleep(1.0)  # simulate network delay
        return MockResponse()

    adapter._client.request = mock.AsyncMock(side_effect=mock_request)

    prompt = "What is the sentiment for AAPL?"

    print("Testing Groq Caching...")

    # Call 1 (Should take 1 second)
    start = time.time()
    res1 = await adapter.generate_response(prompt)
    print(f"Call 1 Time: {time.time() - start:.2f}s | Content: {res1.content}")

    # Call 2 (Should take 0 seconds)
    start = time.time()
    res2 = await adapter.generate_response(prompt)
    print(f"Call 2 Time: {time.time() - start:.2f}s | Content: {res2.content}")

    assert (time.time() - start) < 0.1, "Cache failed!"
    print("Groq Cache Working!")

    await adapter.close()


if __name__ == "__main__":
    asyncio.run(test_llm_cache())
