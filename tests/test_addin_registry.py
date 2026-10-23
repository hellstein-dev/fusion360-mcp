"""``fusion_addin.core.registry``: ``@handler`` decorator and duplicate detection."""

import pytest

from fusion_addin.core import registry as r


def test_decorator_registers_and_returns_function() -> None:
    reg = r.Registry()

    @reg.handler("sketch.create", mutating=True)
    def create(ctx, params):
        return {"ok": 1}

    spec = reg.get("sketch.create")
    assert spec is not None
    assert spec.fn is create
    assert spec.mutating is True
    assert create(None, {}) == {"ok": 1}
    assert "sketch.create" in reg
    assert len(reg) == 1


def test_mutating_defaults_to_false() -> None:
    reg = r.Registry()
    reg.handler("status.get")(lambda ctx, params: {})
    spec = reg.get("status.get")
    assert spec is not None
    assert spec.mutating is False


def test_duplicate_name_rejected() -> None:
    reg = r.Registry()
    reg.handler("body.list")(lambda ctx, params: {})
    with pytest.raises(r.DuplicateHandlerError, match="already registered"):
        reg.handler("body.list")(lambda ctx, params: {})


@pytest.mark.parametrize("name", ["status", "Status.get", "status.Get", "a.b.c", ".get", "x-y.z"])
def test_method_name_format(name: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        r.Registry().register(name, lambda ctx, params: {})


def test_unknown_name_and_sorted_names() -> None:
    reg = r.Registry()
    reg.register("b.x", lambda ctx, params: {})
    reg.register("a.y", lambda ctx, params: {})
    assert reg.get("c.z") is None
    assert reg.names() == ["a.y", "b.x"]


def test_module_level_handler_uses_addin_registry() -> None:
    @r.handler("test_registry.module_level")
    def fn(ctx, params):
        return {}

    try:
        spec = r.REGISTRY.get("test_registry.module_level")
        assert spec is not None
        assert spec.fn is fn
    finally:
        r.REGISTRY._handlers.pop("test_registry.module_level")
