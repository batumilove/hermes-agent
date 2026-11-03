"""Compatibility alias for contributor tools; runtime code lives in the installed package."""
import sys
from hermes_cli import fork_provenance

sys.modules[__name__] = fork_provenance
