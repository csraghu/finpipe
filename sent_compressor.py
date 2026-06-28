import asyncio

from llmlingua import PromptCompressor

# NOTE: Production logic lives in finpipe.providers.llm_base,
# finpipe.core.llm_compress, and finpipe.core.llm_prompt.
# Runs automatically inside every ILLMProvider.generate_response().


class SentimentCompressor:
    def __init__(self, model_name: str = "Pretrained-Match", device: str = "cpu"):
        """
        Initializes the compressor. For standard local machines, running a
        lightweight model on the CPU is highly efficient.
        """
        # Defaults to a small, fast model if not specified
        self.compressor = PromptCompressor(device_map=device)

    async def compress_for_sentiment(self, raw_text: str, target_ratio: float = 0.5) -> str:
        """
        Compresses input text specifically targeting sentiment preservation.

        :param raw_text: The long, unrefined input string or API payload.
        :param target_ratio: The target size relative to original (0.5 = 50% savings).
        """
        # Explicitly declare the task instruction and question
        instruction = (
            "Analyze the text carefully to determine the underlying market or emotional sentiment."
        )
        question = "What is the precise sentiment, tone, and direction expressed in this text?"

        # Wrap synchronous library call in an executor if running in an async pipeline
        loop = asyncio.get_event_loop()

        def _run_compression():
            return self.compressor.compress_prompt(
                context=[raw_text],
                instruction=instruction,
                question=question,
                rate=target_ratio,
                # Key settings for task-aware conditioning:
                condition_compare=True,
                condition_in_question="after",
                reorder_context="sort",
                force_tokens=["\n", ".", "!", "?"],  # Maintain structural punctuation
            )

        results = await loop.run_in_executor(None, _run_compression)

        # Return the optimized prompt string ready for your primary LLM
        return results["compressed_prompt"]


# --- Quick Test Execution ---
async def main():
    compressor = SentimentCompressor(device="cpu")

    verbose_input = (
        "The recent quarterly report indicates that while foundational metrics are stable, "
        "the sudden, unexpected resignation of the lead architect has introduced an incredibly "
        "high level of anxiety among stakeholders, completely overshadowing the modest 2% revenue growth "
        "and triggering an immediate, intense wave of panic selling in the aftermarket sessions."
    )

    compressed_output = await compressor.compress_for_sentiment(verbose_input, target_ratio=0.5)

    print("--- Original Text ---")
    print(verbose_input)
    print("\n--- Sentiment-Preserved Compressed Text ---")
    print(compressed_output)


if __name__ == "__main__":
    asyncio.run(main())
