# Design Principles

## 1. Interface-First Design
- Every component is defined by an **interface** (ABC) in `src/carcharoth/interfaces/`
- Concrete implementations live in focused modules under `services/`, `strategies/`, `risk/`, etc.
- Wiring happens in **exactly one place**: `src/carcharoth/main.py` (the composition root)
- Swapping a component never requires changes anywhere else — just change the wiring

**Why:** This makes testing trivial (use fakes), refactoring safe, and swapping providers (broker,
data source, strategies) a localized change.

## 2. Type-First Development
- Strict mypy enabled (`tool.mypy.strict = true`)
- All function signatures are fully annotated
- Generic types and union types are preferred over `Any`
- Domain models are defined in `domain/models.py` and used consistently across layers

**Why:** Type checking catches bugs early and serves as machine-readable documentation.

## 3. Registry Pattern for Extensibility
- Strategies register themselves in `strategies/registry.py`
- Regime features register in `regime/registry.py`
- Regime detectors register in `regime/detectors.py` (`build_detector` maps
  `regime.detector` config to a concrete `RegimeDetector`)
- New implementations can be added without touching the composition root

**How to add a strategy:**
1. Create a new module under `strategies/`
2. Implement the `Strategy` ABC from `interfaces/strategy.py`
3. Add one line to `strategies/registry.py`: `_STRATEGIES["your_name"] = YourStrategy`
4. Add it to `config/config.yaml` under `strategies:` (keyed by name) and set `active: true`
   (single-strategy mode) or reference it from `regime.regimes` (regime-driven mode)

## 4. Layered Architecture
- **Interfaces** (`interfaces/`) — contracts
- **Domain models** (`domain/models.py`) — pure data, no I/O
- **Services** (`services/`) — provider implementations, type conversions at boundaries
- **Engine** (`engine/`) — orchestration logic
- **Persistence** (`persistence/`) — data access (SQLAlchemy)
- **Config** (`config/`) — Pydantic schemas, YAML loading

**Why:** Clear separation of concerns makes each layer testable and swappable.

For module-level detail and data flows, see [architecture.md](../architecture.md).
