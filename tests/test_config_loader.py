from pathlib import Path

import pytest

from carcharoth.config.loader import OVERRIDES_SOURCE, ConfigError, load_profile, resolve_raw

PROJECT_BASE = (Path(__file__).parent.parent / "config" / "base.yaml").read_text()


def write(root: Path, name: str, text: str) -> None:
    path = root / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    write(tmp_path, "base", PROJECT_BASE)
    write(tmp_path, "symbols/small", "symbols: [AAPL, MSFT]\n")
    write(
        tmp_path,
        "profiles/test",
        "extends: [base, symbols/small]\nmodel:\n  decision: {long_entry_threshold: 0.001}\n",
    )
    return tmp_path


def test_recursive_merge_keeps_model_siblings(config_dir: Path) -> None:
    model = load_profile("test", config_dir=config_dir).config.model
    assert model.decision.long_entry_threshold == 0.001
    assert model.decision.short_entry_threshold == -0.0005


def test_lists_replace_wholesale(config_dir: Path) -> None:
    write(config_dir, "profiles/other", "extends: [base, symbols/small]\nsymbols: [SPY]\n")
    assert load_profile("other", config_dir=config_dir).config.symbols == ["SPY"]


def test_extends_order_later_wins(config_dir: Path) -> None:
    write(config_dir, "layers/a", "risk: {max_open_positions: 1}\n")
    write(config_dir, "layers/b", "risk: {max_open_positions: 2}\n")
    write(config_dir, "profiles/ab", "extends: [base, symbols/small, layers/a, layers/b]\n")
    assert load_profile("ab", config_dir=config_dir).config.risk.max_open_positions == 2


def test_cycle_and_depth_are_rejected(config_dir: Path) -> None:
    write(config_dir, "layers/x", "extends: [layers/y]\n")
    write(config_dir, "layers/y", "extends: [layers/x]\n")
    write(config_dir, "profiles/cyclic", "extends: [base, layers/x]\n")
    with pytest.raises(ConfigError, match="cycle"):
        load_profile("cyclic", config_dir=config_dir)


def test_unknown_path_names_source(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/typo",
        "extends: [base, symbols/small]\nrisk: {max_open_positionz: 3}\n",
    )
    with pytest.raises(ConfigError, match=r"typo\.yaml.*max_open_positionz"):
        load_profile("typo", config_dir=config_dir)


def test_null_artifact_path_is_open_slot(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/artifact",
        "extends: [base, symbols/small]\nmodel: {artifact_path: artifacts/models/m1}\n",
    )
    assert str(load_profile("artifact", config_dir=config_dir).config.model.artifact_path).endswith(
        "artifacts/models/m1"
    )


def test_target_is_atomic(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/classify",
        "extends: [base, symbols/small]\n"
        "model:\n  target: {type: classification, horizon_bars: 5, neutral_band: 0.001}\n",
    )
    target = load_profile("classify", config_dir=config_dir).config.model.target
    assert target.type == "classification"


def test_search_space_is_atomic_and_paths_are_validated(config_dir: Path) -> None:
    write(
        config_dir,
        "profiles/search",
        "extends: [base, symbols/small]\noptimization:\n"
        "  search_space:\n"
        "    model.decision.long_entry_threshold: {type: float, low: 0.0001, high: 0.002}\n",
    )
    resolved = load_profile("search", config_dir=config_dir)
    assert list(resolved.config.optimization.search_space) == [
        "model.decision.long_entry_threshold"
    ]
    write(
        config_dir,
        "profiles/bad-search",
        "extends: [base, symbols/small]\noptimization:\n"
        "  search_space:\n    model.nope.value: {type: int, low: 1, high: 2}\n",
    )
    with pytest.raises(ConfigError, match=r"model\.nope"):
        load_profile("bad-search", config_dir=config_dir)


def test_set_override_and_provenance(config_dir: Path) -> None:
    resolved = load_profile("test", {"risk.max_open_positions": 12}, config_dir=config_dir)
    assert resolved.config.risk.max_open_positions == 12
    assert resolved.provenance["risk.max_open_positions"] == OVERRIDES_SOURCE


def test_resolve_raw_bare_base(config_dir: Path) -> None:
    raw, layers, _ = resolve_raw("base", config_dir=config_dir)
    assert raw["model"]["benchmark_symbol"] == "SPY"
    assert layers == [str(config_dir / "base.yaml")]
