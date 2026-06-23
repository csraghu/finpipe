import json
import os

from finpipe.core.config import FinpipeConfig

# Create a dummy json override file
override_data = {
    "providers": {
        "yahoo": {
            "rate_limits": {
                "max_requests_per_second": 42.0
            }
        },
        "alpha_vantage": {
            "ttls": {
                "historical_prices_sec": 9999
            }
        }
    }
}

with open("test_overrides.json", "w", encoding="utf-8") as f:
    json.dump(override_data, f)

# Load it
config = FinpipeConfig.from_json("test_overrides.json")

yahoo_rps = config.providers.yahoo.rate_limits.max_requests_per_second
print("Yahoo Rate Limit (Expected 42.0):", yahoo_rps)
av_ttl = config.providers.alpha_vantage.ttls.historical_prices_sec
print("AlphaVantage TTL (Expected 9999):", av_ttl)
fred_rps = config.providers.fred.rate_limits.max_requests_per_second
print("Fred Rate Limit (Expected default 2.0):", fred_rps)

os.remove("test_overrides.json")
