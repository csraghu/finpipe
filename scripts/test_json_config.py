import os
from finpipe.core.config import FinpipeConfig
import json

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

print("Yahoo Rate Limit (Expected 42.0):", config.providers.yahoo.rate_limits.max_requests_per_second)
print("AlphaVantage TTL (Expected 9999):", config.providers.alpha_vantage.ttls.historical_prices_sec)
print("Fred Rate Limit (Expected default 2.0):", config.providers.fred.rate_limits.max_requests_per_second)

os.remove("test_overrides.json")
