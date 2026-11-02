"""Import-safe shared layer merger; no IO or environment expansion."""
def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*: dict-over-dict recurses (so overriding one leaf
    keeps sibling defaults), and ``None`` over a dict section is ignored.

    An empty section key in config.yaml (``terminal:`` with no value) parses as YAML ``None``; treating that
    as an override would replace the entire default dict with ``None`` and crash every downstream consumer
    that expects a mapping (#58277).
    """
    result = base.copy()
    for key, value in override.items():
        over_dict = isinstance(result.get(key), dict)
        if over_dict and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif not (over_dict and value is None):
            result[key] = value
    return result

def resolve_boolean_leaf(defaults, user, managed, section, key):
    """Project a validated literal boolean only; never visit unrelated values."""
    def project(layer):
        part = layer.get(section, {})
        if not isinstance(part, dict):
            raise ValueError("section must be a mapping")
        if key not in part:
            return {}
        if type(part[key]) is not bool:
            raise ValueError("leaf must be a literal boolean")
        return {section: {key: part[key]}}
    return _deep_merge(_deep_merge(project(defaults), project(user)), project(managed))[section][key]
