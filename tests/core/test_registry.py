import pytest
from finpipe.core.registry import BuildContext, ProviderRegistry, register_provider


def test_provider_registry_register_build_and_names():
    registry: ProviderRegistry[str] = ProviderRegistry()

    @registry.register("alpha")
    def _alpha(_ctx: BuildContext) -> str:
        return "alpha"

    assert registry.names() == ["alpha"]
    assert registry.build(BuildContext(config={}), "alpha") == "alpha"
    assert registry.get("missing") is None
    with pytest.raises(KeyError, match="Provider not registered"):
        registry.build(BuildContext(config={}), "missing")


def test_register_provider_decorator():
    @register_provider("demo", category="equity")
    def _demo(_ctx: BuildContext) -> str:
        return "demo"

    from finpipe.core.registry import EQUITY_REGISTRY

    assert EQUITY_REGISTRY.build(BuildContext(config={}), "demo") == "demo"
    assert _demo is not None
