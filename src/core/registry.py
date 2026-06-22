from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class BuildContext:
    config: Any


ProviderFactory = Callable[[BuildContext], T]


class ProviderRegistry(Generic[T]):
    """Generic decorator-driven provider registry (aksh pattern)."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory[T]] = {}

    def register(self, name: str) -> Callable[[ProviderFactory[T]], ProviderFactory[T]]:
        def decorator(factory: ProviderFactory[T]) -> ProviderFactory[T]:
            self._factories[name] = factory
            return factory

        return decorator

    def get(self, name: str) -> ProviderFactory[T] | None:
        return self._factories.get(name)

    def build(self, ctx: BuildContext, name: str) -> T:
        factory = self.get(name)
        if factory is None:
            raise KeyError(f"Provider not registered: {name}")
        return factory(ctx)

    def names(self) -> list[str]:
        return list(self._factories.keys())


EQUITY_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()
OPTIONS_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()
MACRO_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()
INTEL_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()
SCREENER_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()
LLM_REGISTRY: ProviderRegistry[Any] = ProviderRegistry()

_CATEGORY_REGISTRIES: dict[str, ProviderRegistry[Any]] = {
    "equity": EQUITY_REGISTRY,
    "options": OPTIONS_REGISTRY,
    "macro": MACRO_REGISTRY,
    "intel": INTEL_REGISTRY,
    "screener": SCREENER_REGISTRY,
    "llm": LLM_REGISTRY,
}


def register_provider(name: str, *, category: str) -> Callable[[ProviderFactory[Any]], ProviderFactory[Any]]:
    registry = _CATEGORY_REGISTRIES[category]

    def decorator(factory: ProviderFactory[Any]) -> ProviderFactory[Any]:
        return registry.register(name)(factory)

    return decorator
