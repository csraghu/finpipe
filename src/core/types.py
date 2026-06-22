from typing import Literal

import pandas as pd
import polars as pl

Interval = Literal["1m", "5m", "15m", "1h", "1d", "1wk", "1mo"]
DataFrameFormat = Literal["polars", "pandas"]
DataFrameLike = pl.DataFrame | pd.DataFrame
