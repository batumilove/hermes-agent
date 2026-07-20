from pathlib import Path

from scripts.check_agents_context import MAX_AGENTS_CHARS, validate_contract


REFERENCE_LINK = (
    "Read [`docs/AGENTS_REFERENCE.md`](docs/AGENTS_REFERENCE.md) "
    "for the detailed version of this guide, including:"
)


def _write_contract(tmp_path: Path, agents: str, reference: str = "# Detailed reference\n"):
    agents_path = tmp_path / "AGENTS.md"
    reference_path = tmp_path / "docs" / "AGENTS_REFERENCE.md"
    reference_path.parent.mkdir(parents=True)
    agents_path.write_text(agents, encoding="utf-8")
    reference_path.write_text(reference, encoding="utf-8")
    return agents_path, reference_path


def test_contract_accepts_compact_agents_with_expanded_reference(tmp_path):
    agents_path, reference_path = _write_contract(
        tmp_path,
        f"# Agent guide\n\n{REFERENCE_LINK}\n",
    )

    assert validate_contract(agents_path, reference_path) == []


def test_contract_accepts_exact_character_limit(tmp_path):
    prefix = f"# Agent guide\n\n{REFERENCE_LINK}\n"
    agents_path, reference_path = _write_contract(
        tmp_path,
        prefix + "x" * (MAX_AGENTS_CHARS - len(prefix)),
    )

    assert validate_contract(agents_path, reference_path) == []


def test_contract_rejects_agents_above_context_floor(tmp_path):
    agents_path, reference_path = _write_contract(
        tmp_path,
        f"# Agent guide\n\n{REFERENCE_LINK}\n" + "x" * MAX_AGENTS_CHARS,
    )

    errors = validate_contract(agents_path, reference_path)

    assert any("exceeds 20000-character contract" in error for error in errors)


def test_contract_rejects_missing_expanded_reference_link(tmp_path):
    agents_path, reference_path = _write_contract(tmp_path, "# Agent guide\n")

    errors = validate_contract(agents_path, reference_path)

    assert any("must contain the canonical expanded-reference line" in error for error in errors)


def test_contract_rejects_bare_reference_path_without_markdown_link(tmp_path):
    agents_path, reference_path = _write_contract(
        tmp_path,
        "# Agent guide\n\nDo not link docs/AGENTS_REFERENCE.md here.\n",
    )

    errors = validate_contract(agents_path, reference_path)

    assert any("must contain the canonical expanded-reference line" in error for error in errors)


def test_contract_rejects_alternate_link_without_canonical_line(tmp_path):
    agents_path, reference_path = _write_contract(
        tmp_path,
        "# Agent guide\n\n[Other reference](docs/AGENTS_REFERENCE.md)\n",
    )

    errors = validate_contract(agents_path, reference_path)

    assert any("must contain the canonical expanded-reference line" in error for error in errors)


def test_contract_rejects_reference_mentions_without_canonical_line(tmp_path):
    cases = {
        "image": "![Expanded reference](docs/AGENTS_REFERENCE.md)\n",
        "inline-code": "`[Expanded reference](docs/AGENTS_REFERENCE.md)`\n",
        "inline-code-long": "``[Expanded reference](docs/AGENTS_REFERENCE.md)``\n",
        "escaped-link": "\\[Expanded reference](docs/AGENTS_REFERENCE.md)\n",
        "html-comment": "<!-- [Expanded reference](docs/AGENTS_REFERENCE.md) -->\n",
        "fenced-code": (
            "```markdown\n"
            "[Expanded reference](docs/AGENTS_REFERENCE.md)\n"
            "```\n"
        ),
        "fenced-code-long": (
            "````markdown\n"
            "[Expanded reference](docs/AGENTS_REFERENCE.md)\n"
            "```\n"
            "````\n"
        ),
        "fenced-code-tilde": (
            "~~~~markdown\n"
            "[Expanded reference](docs/AGENTS_REFERENCE.md)\n"
            "~~~~\n"
        ),
    }

    for name, agents in cases.items():
        agents_path, reference_path = _write_contract(tmp_path / name, agents)

        errors = validate_contract(agents_path, reference_path)

        assert any("must contain the canonical expanded-reference line" in error for error in errors)


def test_contract_rejects_missing_expanded_reference(tmp_path):
    agents_path, reference_path = _write_contract(
        tmp_path,
        f"# Agent guide\n\n{REFERENCE_LINK}\n",
    )
    reference_path.unlink()

    errors = validate_contract(agents_path, reference_path)

    assert any("expanded reference is missing" in error for error in errors)


def test_repository_agents_context_contract():
    repo_root = Path(__file__).resolve().parents[2]

    assert validate_contract(
        repo_root / "AGENTS.md",
        repo_root / "docs" / "AGENTS_REFERENCE.md",
    ) == []
