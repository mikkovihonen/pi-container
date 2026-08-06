# Development

The host-side Python (`src/`) is managed with [uv](https://docs.astral.sh/uv/). Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.

```bash
# Provision / update the environment (runtime deps + the default `dev` group)
uv sync

# Run the project's test suite (src/tests)
uv run pytest

# Lint / format
uv run ruff check src
uv run ruff format src

# Run the mitmproxy proxy-addon tests (heavy `mitmproxy` dep — opt-in group)
uv run --group proxy pytest pi-coding-agent-proxy/addons
```

The Python sources run directly from `src/`. uv treats the project as a
*virtual* project via `[tool.uv] package = false`. Dependencies are installed
into `.venv`. The project itself is not built or installed. `build.sh` and
`run.sh` wrap `uv run --project <repo>`. They use this environment while
still operating on the caller's working directory.

<a name="coverage"></a>
## Coverage

CI enforces test coverage (minimum 90%). The system measures coverage with `pytest-cov`. The CI auto-commits a badge SVG to `docs/assets/coverage.svg` on every push to `main`.

Run locally:

```bash
uv run pytest --cov --cov-report=term-missing
```

<a name="schema-validation"></a>
## Schema validation

The system validates per-project configuration (`.pi-container/config.yaml`) at launch
against the pi-container version and the expected schema. The validation checks:

1. **Schema version** — The `schema_version` field in the config matches the
   app version (from the latest git tag). Bump this field in the template
   (`pi-coding-agent/default/config.yaml`) whenever you change the schema.
2. **Required fields** — All fields defined in the schema must be present with
   the correct types.

Validation failure makes the launch exit with a clear error message. The message lists the
issues and suggests the fix (delete `.pi-container` and re-run).

The validation logic lives in `src/config_schema.py` and is tested in
`src/tests/test_config_schema.py`.
