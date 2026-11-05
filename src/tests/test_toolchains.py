import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

sys.dont_write_bytecode = True

from toolchains import (
    AVAILABLE_TOOLS,
    CORE_TOOLS,
    ON_DEMAND_TOOLS,
    compute_tool_hash,
    tool_recipe_path,
    tool_to_build_arg,
    tool_to_context_name,
    tool_to_image_tag,
    tool_to_stage_target,
)


def test_tool_collections():
    assert frozenset({"go", "rust"}) == CORE_TOOLS
    assert "ripgrep" in ON_DEMAND_TOOLS
    assert "mold" in ON_DEMAND_TOOLS
    assert "git" in ON_DEMAND_TOOLS
    assert AVAILABLE_TOOLS == (CORE_TOOLS | ON_DEMAND_TOOLS)
    assert len(AVAILABLE_TOOLS) == 16


def test_tool_mapping_helpers():
    assert tool_to_build_arg("go") == "WITH_GO"
    assert tool_to_build_arg("tree-sitter-cli") == "WITH_TREE_SITTER_CLI"
    assert tool_to_build_arg("sqlite-vec") == "WITH_SQLITE_VEC"

    assert tool_to_context_name("ripgrep") == "tool_ripgrep"
    assert tool_to_context_name("tree-sitter-cli") == "tool_tree_sitter_cli"

    assert tool_to_image_tag("mold") == "pi-coding-agent-tool-mold:local"
    assert tool_to_stage_target("mold") == "stage-mold"


def test_tool_recipe_path(tmp_path: Path):
    go_path = tool_recipe_path("go", tmp_path)
    assert go_path == tmp_path / "pi-coding-agent-builder" / "build-go.sh"

    mold_path = tool_recipe_path("mold", tmp_path)
    assert mold_path == tmp_path / "pi-coding-agent-builder" / "tools" / "mold" / "build.sh"


def test_compute_tool_hash(tmp_path: Path):
    repo = tmp_path
    builder_dir = repo / "pi-coding-agent-builder"
    tools_dir = builder_dir / "tools" / "mold"
    tools_dir.mkdir(parents=True)
    recipe = tools_dir / "build.sh"
    recipe.write_text("#!/bin/bash\necho mold v1\n")

    containerfile = builder_dir / "Containerfile"
    containerfile.write_text(
        "FROM deps AS mold\n"
        "ARG MOLD_VERSION=1.0\n"
        "RUN bash build.sh\n"
        "FROM scratch AS stage-mold\n"
        "COPY --from=mold /out/ /\n"
    )

    hash1 = compute_tool_hash("mold", repo)
    assert len(hash1) == 16

    # Change recipe
    recipe.write_text("#!/bin/bash\necho mold v2\n")
    hash2 = compute_tool_hash("mold", repo)
    assert hash1 != hash2

    # Change pin in Containerfile
    containerfile.write_text(
        "FROM deps AS mold\n"
        "ARG MOLD_VERSION=2.0\n"
        "RUN bash build.sh\n"
        "FROM scratch AS stage-mold\n"
        "COPY --from=mold /out/ /\n"
    )
    hash3 = compute_tool_hash("mold", repo)
    assert hash2 != hash3
