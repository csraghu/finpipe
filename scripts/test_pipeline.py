import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from finpipe.client import Client
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import (
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)

# Configure extensive logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/finpipe_pipeline.log", mode="w"),
    ],
)
logger = logging.getLogger("finpipe_pipeline")


def _load_env():
    # Look for .env starting from the current working directory and traversing upwards
    current = Path.cwd().resolve()
    candidates = [current / ".env", current.parent / ".env", current.parent.parent / ".env"]
    for env_path in candidates:
        if env_path.is_file():
            logger.info(f"Loaded API keys from {env_path}")
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = [x.strip() for x in line.split("=", 1)]
                    if val.startswith(('"', "'")) and val.endswith(('"', "'")) and len(val) >= 2:
                        val = val[1:-1]
                    if key not in os.environ:
                        os.environ[key] = val
            return


async def run_pipeline():
    logger.info("Initializing Finpipe Test Pipeline...")

    # Automatically load the .env file using aksh-style parsing
    _load_env()

    # Initialize the configuration. We explicitly skip LLMs for this run.
    config = FinpipeConfig(dataframe_format="polars")

    symbols = ["AAPL", "TSLA", "MSFT"]
    macro_series = "CPIAUCSL"  # Consumer Price Index

    summary_stats = {"success": 0, "rate_limited": 0, "network_errors": 0, "other_errors": 0}

    # The Client handles opening and cleanly closing all HTTP connections
    async with Client(config) as client:
        # 1. Fetch Macro Data (Independent of specific tickers)
        logger.info(f"--- Fetching Macro Data: {macro_series} ---")
        try:
            macro_df = await client.fred.get_macro_series(
                macro_series, start_date=date(2020, 1, 1), end_date=date.today()
            )
            logger.info(f"[SUCCESS] FRED Macro Data: {macro_series}. Points: {len(macro_df)}")
            summary_stats["success"] += 1
        except FinpipeRateLimitExceededError as e:
            logger.error(f"[RATE LIMITED] FRED Macro Data: {e}")
            summary_stats["rate_limited"] += 1
        except FinpipeProviderDownError as e:
            logger.error(f"[NETWORK ERROR] FRED Macro Data: {e}")
            summary_stats["network_errors"] += 1
        except Exception as e:
            logger.error(f"[FAILED] FRED Macro Data: {e}", exc_info=True)
            summary_stats["other_errors"] += 1

        # 2. Iterate through Equity Pipeline
        for symbol in symbols:
            logger.info(f"\n--- Processing Ticker: {symbol} ---")

            tasks_to_run = [
                ("Metadata (Yahoo)", client.yahoo.get_metadata(symbol)),
                ("Spot Price (Yahoo)", client.yahoo.get_live_spot_price(symbol)),
                (
                    "Historical Prices (Yahoo)",
                    client.yahoo.get_historical_prices(
                        symbol, date.today() - timedelta(days=365), date.today()
                    ),
                ),
                (
                    "Historical Prices (AlphaVantage)",
                    client.alpha_vantage.get_historical_prices(
                        symbol, date.today() - timedelta(days=365), date.today()
                    ),
                ),
                ("Financial Statements (Yahoo)", client.yahoo.get_financial_statements(symbol)),
                ("Options Chain (Yahoo)", client.yahoo.get_options_chain(symbol)),
                ("Options Snapshot (Massive)", client.massive.get_options_snapshot(symbol)),
                (
                    "News Sentiment (StockTwits/Google)",
                    client.sentiment.get_sentiment_score(symbol),
                ),
            ]

            for task_name, coro in tasks_to_run:
                try:
                    result = await coro
                    # For dataframes or complex objects, just get a brief summary representation
                    res_summary = (
                        str(result)[:50].replace("\n", " ") + "..."
                        if result is not None
                        else "None"
                    )
                    res_summary = res_summary.encode("ascii", "ignore").decode("ascii")
                    logger.info(f"[SUCCESS] {task_name}: {res_summary}")
                    summary_stats["success"] += 1
                except FinpipeRateLimitExceededError as e:
                    logger.error(f"[RATE LIMITED] {task_name} for {symbol}: {e}")
                    summary_stats["rate_limited"] += 1
                except FinpipeProviderDownError as e:
                    logger.error(f"[NETWORK ERROR] {task_name} for {symbol}: {e}")
                    summary_stats["network_errors"] += 1
                except FinpipeDataNotFoundError as e:
                    logger.warning(f"[NOT FOUND] {task_name} for {symbol}: {e}")
                    # We expected this for mocked/demo endpoints, so we intentionally don't print the traceback
                    summary_stats["other_errors"] += 1
                except Exception as e:
                    logger.error(f"[FAILED] {task_name} for {symbol}: {e}", exc_info=True)
                    summary_stats["other_errors"] += 1

    # Print Summary Table
    print("\n" + "=" * 50)
    print("FINPIPE PIPELINE EXECUTION SUMMARY")
    print("=" * 50)
    print(f"Total Successful Fetches : {summary_stats['success']}")
    print(f"Rate Limit Blocks        : {summary_stats['rate_limited']}")
    print(f"Network / Circuit Errors : {summary_stats['network_errors']}")
    print(f"Other Unhandled Errors   : {summary_stats['other_errors']}")
    print("=" * 50)
    print("Check 'finpipe_pipeline.log' for detailed stack traces and responses.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
