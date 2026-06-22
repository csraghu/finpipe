"""Composite capability facades — primary/fallback routing stubs."""

from __future__ import annotations

from finpipe.core.config import FinpipeConfig


class CompositeEquityService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeOptionsService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeMacroService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeIntelService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeScreenerService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeLlmService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config
