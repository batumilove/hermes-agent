"""Provider fallback observers register and dispatch through the plugin manager."""
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


def test_provider_fallback_hooks_dispatch_registered_observers():
    manager = PluginManager()
    manager._discovered = True
    context = PluginContext(PluginManifest(name="fallback-observer", version="1", description="test"), manager)
    observed = []
    hooks = ("on_fallback_activated", "on_fallback_chain_exhausted", "on_primary_restored")
    for hook in hooks:
        context.register_hook(hook, lambda _hook=hook, **_event: observed.append(_hook))
    for hook in hooks:
        manager.invoke_hook(hook, session_id="fallback-session")
    assert observed == list(hooks)
