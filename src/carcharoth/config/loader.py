"""Layered config loader: profile -> `extends` chain -> merge -> validate.

Resolution order for a profile:

    base (transitively via ``extends``, depth-first, in file order)
    -> the profile's own body
    -> CLI ``--set path=value`` overrides

Merge semantics (deliberate, tested):

- scalars: later layer wins
- lists: **replace wholesale** (``symbols: [SPY]`` never concatenates)
- dicts: merge recursively
- the first layer (``base.yaml``) owns the key structure: **no layer above
  it may introduce new keys** — adding/removing a config key is a deliberate
  base-layer edit. Two openings exist:
  - a base value of ``null`` or ``{}`` is an *open slot*: layers may fill it
    freely (e.g. ``backtest.permutation``, free-form ``params`` dicts)
  - ``optimization.search_space`` is *atomic*: a layer defining it replaces
    it wholesale, and its keys are validated as dot-paths into the resolved
    config instead of against the base structure

Every resolved config carries a provenance map (leaf dot-path -> source
file / ``--set``) and a content hash over the canonical JSON dump of the
validated model, so any run is exactly replayable.
"""

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from carcharoth.config.overrides import OverrideError, validate_override_paths
from carcharoth.config.run_config import RunConfig

CONFIG_DIR = Path("config")
MAX_EXTENDS_DEPTH = 8
#: source label for CLI/TUI overrides in the provenance map
OVERRIDES_SOURCE = "--set"
#: mappings replaced wholesale on redefinition (their keys are free-form)
ATOMIC_PATHS = frozenset({"optimization.search_space"})


class ConfigError(Exception):
    """A layer file, merge rule, or override path is invalid."""


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """A fully merged, validated config plus its reproducibility metadata."""

    config: RunConfig
    #: the merged pre-validation dict (the optimizer applies trial overrides here)
    raw: dict[str, Any]
    #: blake2b over the canonical JSON dump of the validated config
    hash: str
    profile: str
    #: layer files in merge order (config-root-relative)
    layers: list[str]
    #: CLI ``--set`` overrides (dot-path -> value)
    overrides: dict[str, Any]
    #: leaf dot-path -> source (layer file or ``--set``)
    provenance: dict[str, str]

    def stamp(self) -> dict[str, Any]:
        """The reproducibility block written into run summary artifacts."""
        return {
            "profile": self.profile,
            "layers": list(self.layers),
            "overrides": dict(self.overrides),
        }


def config_hash(config: RunConfig) -> str:
    """Canonical content hash: stable under key order and YAML formatting."""
    canonical = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


def load_profile(
    profile: str,
    overrides: Mapping[str, Any] | None = None,
    config_dir: Path = CONFIG_DIR,
) -> ResolvedConfig:
    """Resolve, merge, validate, and hash a profile.

    Raises ``ConfigError`` for structural problems (missing files, cycles,
    unknown paths) and pydantic ``ValidationError`` for value problems.
    """
    overrides = dict(overrides or {})
    raw, layers, provenance = resolve_raw(profile, overrides, config_dir)
    config = RunConfig.model_validate(raw)
    _validate_search_space_keys(raw)
    return ResolvedConfig(
        config=config,
        raw=raw,
        hash=config_hash(config),
        profile=profile,
        layers=layers,
        overrides=overrides,
        provenance=provenance,
    )


def resolve_raw(
    profile: str,
    overrides: Mapping[str, Any] | None = None,
    config_dir: Path = CONFIG_DIR,
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """Merge a profile's layers without validating (used by ``config diff``).

    Returns ``(merged_raw, layer_paths, provenance)``.
    """
    path = _profile_path(profile, config_dir)
    layers: list[tuple[Path, dict[str, Any]]] = []
    _collect_layers(path, config_dir, layers, stack=[], visited=set())

    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    base_structure: dict[str, Any] = {}
    for index, (layer_path, body) in enumerate(layers):
        source = _relative(layer_path, config_dir)
        if index == 0:
            base_structure = copy.deepcopy(body)
        _merge_layer(merged, body, "", source, provenance, base_structure, structural=(index == 0))
    if overrides:
        override_layer = _nested(overrides)
        _merge_layer(
            merged, override_layer, "", OVERRIDES_SOURCE, provenance, base_structure, False
        )
    return merged, [_relative(p, config_dir) for p, _ in layers], provenance


def _profile_path(profile: str, config_dir: Path) -> Path:
    """``backtest`` -> ``config/profiles/backtest.yaml``; names containing a
    ``/`` (e.g. ``trading/paper``) are config-root-relative."""
    name = profile.removesuffix(".yaml")
    if "/" in name:
        candidates = [config_dir / f"{name}.yaml"]
    else:
        candidates = [config_dir / "profiles" / f"{name}.yaml", config_dir / f"{name}.yaml"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    looked = ", ".join(str(c) for c in candidates)
    raise ConfigError(f"profile {profile!r} not found (looked at: {looked})")


def _collect_layers(
    path: Path,
    config_dir: Path,
    out: list[tuple[Path, dict[str, Any]]],
    stack: list[Path],
    visited: set[Path],
) -> None:
    """Depth-first ``extends`` resolution: parents merge before the file's
    own body; a file appearing via several chains merges only once."""
    resolved = path.resolve()
    if resolved in stack:
        chain = " -> ".join(str(p) for p in [*stack, resolved])
        raise ConfigError(f"extends cycle: {chain}")
    if len(stack) >= MAX_EXTENDS_DEPTH:
        raise ConfigError(f"extends depth exceeds {MAX_EXTENDS_DEPTH} at {path}")
    if resolved in visited:
        return
    body = _read_layer(path)
    extends = body.pop("extends", [])
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list) or not all(isinstance(e, str) for e in extends):
        raise ConfigError(f"{path}: 'extends' must be a list of layer names")
    stack.append(resolved)
    for entry in extends:
        parent = config_dir / f"{entry.removesuffix('.yaml')}.yaml"
        if not parent.is_file():
            raise ConfigError(f"{path}: extends {entry!r} -> {parent} does not exist")
        _collect_layers(parent, config_dir, out, stack, visited)
    stack.pop()
    visited.add(resolved)
    out.append((path, body))


def _read_layer(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            body = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return body


def _merge_layer(
    target: dict[str, Any],
    overlay: Mapping[str, Any],
    prefix: str,
    source: str,
    provenance: dict[str, str],
    base_structure: dict[str, Any],
    structural: bool,
) -> None:
    for key, value in overlay.items():
        dotted = f"{prefix}{key}"
        if not structural and not _is_allowed(base_structure, dotted):
            raise ConfigError(
                f"{source}: unknown config path {dotted!r} — layers may only override "
                "values; adding or removing keys happens in the base layer"
            )
        if dotted in ATOMIC_PATHS and isinstance(value, Mapping):
            target[key] = copy.deepcopy(dict(value))
            _reassign_subtree(provenance, dotted, source)
        elif isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge_layer(
                target[key], value, f"{dotted}.", source, provenance, base_structure, structural
            )
        else:
            target[key] = copy.deepcopy(value)
            _record(provenance, value, dotted, source)


def _is_allowed(base_structure: Any, dotted: str) -> bool:
    """True when the path exists in the base layer's structure, or descends
    into an open slot (a base value of ``null`` or ``{}``)."""
    node: Any = base_structure
    for segment in dotted.split("."):
        if node is None or (isinstance(node, dict) and not node):
            return True  # open slot: the base deliberately left this free-form
        if not isinstance(node, dict) or segment not in node:
            return False
        node = node[segment]
    return True


def _record(provenance: dict[str, str], value: Any, dotted: str, source: str) -> None:
    """Attribute every leaf under ``dotted`` to ``source``; a non-dict value
    (scalar or list) is itself the leaf."""
    if isinstance(value, Mapping) and value:
        for key, child in value.items():
            _record(provenance, child, f"{dotted}.{key}", source)
    else:
        _reassign_subtree(provenance, dotted, source)


def _reassign_subtree(provenance: dict[str, str], dotted: str, source: str) -> None:
    """Point ``dotted`` at ``source`` and drop stale deeper entries (the
    subtree was replaced wholesale)."""
    for stale in [k for k in provenance if k.startswith(f"{dotted}.")]:
        del provenance[stale]
    provenance[dotted] = source


def _nested(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """``{"risk.max_open_positions": 12}`` -> ``{"risk": {"max_open_positions": 12}}``.

    Note: dot-paths cannot address search-space keys (which themselves
    contain dots); replace ``optimization.search_space`` via a layer file.
    """
    root: dict[str, Any] = {}
    for path, value in overrides.items():
        segments = path.split(".")
        node = root
        for segment in segments[:-1]:
            child = node.setdefault(segment, {})
            if not isinstance(child, dict):
                raise ConfigError(f"conflicting --set paths at {path!r}")
            node = child
        node[segments[-1]] = value
    return root


def _validate_search_space_keys(raw: dict[str, Any]) -> None:
    """Search-space keys are dot-paths into the *resolved* config (the
    carve-out from the base-owns-structure rule); fail fast on typos."""
    optimization = raw.get("optimization")
    if not isinstance(optimization, dict):
        return
    search_space = optimization.get("search_space")
    if not isinstance(search_space, dict):
        return
    try:
        validate_override_paths(raw, search_space.keys())
    except OverrideError as exc:
        raise ConfigError(f"optimization.search_space: {exc}") from exc


def _relative(path: Path, config_dir: Path) -> str:
    try:
        return str(config_dir / path.resolve().relative_to(config_dir.resolve()))
    except ValueError:
        return str(path)
