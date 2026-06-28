import asyncio
import os
import sys

from finpipe.client import Client

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()


async def main():
    async with Client() as client:
        print("Pinging screener.finviz probe...")
        result = await client.health.ping_probe("screener.finviz")
        print(f"Status: {result.status}")
        print(f"Message: {result.message}")
        print(f"Latency: {result.latency_ms} ms")
        print(f"Is OK: {result.ok}")


if __name__ == "__main__":
    asyncio.run(main())
