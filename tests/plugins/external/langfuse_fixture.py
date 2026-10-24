"""Exact-source import helper for reviewed external Langfuse behavior fixtures."""
from pathlib import Path
import importlib.util
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = Path(__file__).parent / "fixtures" / "langfuse-e1f1614"
MODULE_NAME = "frozen_langfuse_plugin"


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
