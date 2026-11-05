---
name: upgrade-toolchains
description: Audits and upgrades source-compiled tools and runtimes in pi-coding-agent-builder (Go, Rust, Node, Python, mold, git, ripgrep, AST/LSP tools, vector DBs) to their latest upstream releases. Use when the user asks to "check for tool updates", "upgrade toolchains", "update ripgrep/mold/git", or "upgrade all tools".
---

# Upgrade Toolchains and Source-Built Runtimes

## When to Use

Use this skill whenever the user asks to inspect, audit, or upgrade any of the tools or runtimes built from source in `pi-coding-agent-builder`. Examples:
- "Check for toolchain updates"
- "Are any of our source-built tools outdated?"
- "Upgrade ripgrep to the latest version"
- "Upgrade mold and git"
- "Upgrade all LSPs"
- "Upgrade all tools"

## Available Tool Groups

- **`compilers`**: `mold`, `go`, `rust`
- **`search`**: `ripgrep`, `fd`, `ast-grep`, `tree-sitter-cli`, `colgrep`
- **`vector`**: `qdrant`, `sqlite-vec`
- **`lsp`**: `rust-analyzer`, `gopls`, `clangd`, `zls`
- **`vcs`**: `git`, `ruby`
- **`core`**: Core runtime dependencies (`go`, `rust`, `node`, `podman`, `netavark`, `aardvark-dns`, `uv`, `podman-compose`, `python-dotenv`, `pyyaml`)

## Workflows

### 1. Audit Available Upgrades (Read-Only)

To audit all 20+ tools and print an aligned comparison table showing Current vs. Upstream releases:

```bash
uv run python3 .pi/skills/upgrade-toolchains/scripts/check_upgrades.py --check
```

Present the table to the user and highlight which tools have available upgrades.

### 2. Upgrading a Specific Tool

To upgrade a single tool (e.g. `mold`):

```bash
uv run python3 .pi/skills/upgrade-toolchains/scripts/check_upgrades.py --upgrade mold --verify
```

The script will:
- Query upstream git tags/manifests to resolve the latest version and peeled commit hash.
- Update the corresponding `ARG MOLD_VERSION=...` and `ARG MOLD_COMMIT=...` in `pi-coding-agent-builder/Containerfile`.
- Execute `src/tests/test_build.py` (`TestToolchainPinsAreDeclared`) to guarantee all pin contracts are preserved.

### 3. Upgrading a Functional Group

To upgrade an entire domain together (e.g. all LSPs or search tools):

```bash
uv run python3 .pi/skills/upgrade-toolchains/scripts/check_upgrades.py --upgrade-group lsp --verify
```

### 4. Upgrading All Outdated Tools

```bash
uv run python3 .pi/skills/upgrade-toolchains/scripts/check_upgrades.py --upgrade all --verify
```

### 5. Verification & Pre-Commit Checks

After applying upgrades:
1. Verify contract tests pass:
    ```bash
    uv run pytest src/tests/test_build.py -k TestToolchainPinsAreDeclared
    ```
2. Verify builder target compilation (e.g. for `sqlite-vec` or the upgraded tool):
    ```bash
    podman build --target stage-<tool> -t pi-coding-agent-tool-<tool>:test -f pi-coding-agent-builder/Containerfile .
    podman rmi pi-coding-agent-tool-<tool>:test
    ```
3. Run all workspace pre-commit checks:
    ```bash
    uv run pre-commit run --all-files
    ```
4. Commit the changes:
    ```bash
    git add pi-coding-agent-builder/Containerfile
    git commit -m "chore(toolchains): upgrade <tool> to <new_version>"
    ```

## Error Handling & Safety

- **Pin mismatch failure**: If `test_build.py` fails after an upgrade, a build script requires an environment variable that was not updated or vice versa. The upgrade script will automatically revert `Containerfile` if `--verify` fails.
- **Rollback**: To discard changes before committing:
    ```bash
    git checkout pi-coding-agent-builder/Containerfile
    ```

