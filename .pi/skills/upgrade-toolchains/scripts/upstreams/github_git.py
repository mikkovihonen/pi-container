import re
import subprocess
import sys

sys.dont_write_bytecode = True

from upstreams.base import ToolReleaseInfo, parse_version_tuple


def get_git_tags_and_commits(repo_url: str) -> dict[str, str]:
    """Fetch all tags and their peeled commit hashes from a remote git repository."""
    cmd = ["git", "ls-remote", "--tags", repo_url]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)

    direct_commits: dict[str, str] = {}
    peeled_commits: dict[str, str] = {}

    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        commit, ref = parts[0], parts[1]
        if not ref.startswith("refs/tags/"):
            continue

        tag_name = ref.removeprefix("refs/tags/")
        if tag_name.endswith("^{}"):
            peeled_commits[tag_name.removesuffix("^{}")] = commit
        else:
            direct_commits[tag_name] = commit

    # Peeled commit takes priority because it points to the underlying commit object
    resolved = {**direct_commits, **peeled_commits}
    return resolved


def find_latest_tag(
    tags_and_commits: dict[str, str],
    pattern: str = r"^v?\d+\.\d+(?:\.\d+)?$",
    exclude_pattern: str | None = r"(?:-rc|-alpha|-beta|-pre|\^)",
) -> tuple[str, str]:
    """Find the highest semver tag matching pattern from a resolved tags dictionary."""
    candidates: list[str] = []
    pat = re.compile(pattern)
    ex_pat = re.compile(exclude_pattern) if exclude_pattern else None

    for tag in tags_and_commits:
        if ex_pat and ex_pat.search(tag):
            continue
        if pat.match(tag):
            candidates.append(tag)

    if not candidates:
        raise ValueError("No tags matched the given criteria")

    candidates.sort(key=parse_version_tuple)
    latest_tag = candidates[-1]
    return latest_tag, tags_and_commits[latest_tag]


def resolve_mold() -> ToolReleaseInfo:
    repo = "https://github.com/rui314/mold.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="mold",
        version=tag,
        args={"MOLD_VERSION": tag, "MOLD_COMMIT": commit},
        category="compilers",
        release_url=f"https://github.com/rui314/mold/releases/tag/{tag}",
    )


def resolve_ripgrep() -> ToolReleaseInfo:
    repo = "https://github.com/BurntSushi/ripgrep.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="ripgrep",
        version=tag,
        args={"RIPGREP_VERSION": tag, "RIPGREP_COMMIT": commit},
        category="search",
        release_url=f"https://github.com/BurntSushi/ripgrep/releases/tag/{tag}",
    )


def resolve_fd() -> ToolReleaseInfo:
    repo = "https://github.com/sharkdp/fd.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="fd",
        version=tag,
        args={"FD_VERSION": tag, "FD_COMMIT": commit},
        category="search",
        release_url=f"https://github.com/sharkdp/fd/releases/tag/{tag}",
    )


def resolve_ast_grep() -> ToolReleaseInfo:
    repo = "https://github.com/ast-grep/ast-grep.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="ast-grep",
        version=tag,
        args={"AST_GREP_VERSION": tag, "AST_GREP_COMMIT": commit},
        category="search",
        release_url=f"https://github.com/ast-grep/ast-grep/releases/tag/{tag}",
    )


def resolve_tree_sitter_cli() -> ToolReleaseInfo:
    repo = "https://github.com/tree-sitter/tree-sitter.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="tree-sitter-cli",
        version=tag,
        args={"TREE_SITTER_VERSION": tag, "TREE_SITTER_COMMIT": commit},
        category="search",
        release_url=f"https://github.com/tree-sitter/tree-sitter/releases/tag/{tag}",
    )


def resolve_colgrep() -> ToolReleaseInfo:
    repo = "https://github.com/lightonai/next-plaid.git"
    cmd = ["git", "ls-remote", repo, "HEAD"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    commit = res.stdout.split()[0]
    return ToolReleaseInfo(
        tool="colgrep",
        version="v0.1.0",
        args={"COLGREP_VERSION": "v0.1.0", "COLGREP_COMMIT": commit},
        category="search",
        release_url="https://github.com/lightonai/next-plaid",
    )


def resolve_qdrant() -> ToolReleaseInfo:
    repo = "https://github.com/qdrant/qdrant.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="qdrant",
        version=tag,
        args={"QDRANT_VERSION": tag, "QDRANT_COMMIT": commit},
        category="vector",
        release_url=f"https://github.com/qdrant/qdrant/releases/tag/{tag}",
    )


def resolve_sqlite_vec() -> ToolReleaseInfo:
    repo = "https://github.com/asg017/sqlite-vec.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="sqlite-vec",
        version=tag,
        args={"SQLITE_VEC_VERSION": tag, "SQLITE_VEC_COMMIT": commit},
        category="vector",
        release_url=f"https://github.com/asg017/sqlite-vec/releases/tag/{tag}",
    )


def resolve_rust_analyzer() -> ToolReleaseInfo:
    repo = "https://github.com/rust-lang/rust-analyzer.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^\d{4}-\d{2}-\d{2}$")
    return ToolReleaseInfo(
        tool="rust-analyzer",
        version=tag,
        args={"RUST_ANALYZER_VERSION": tag, "RUST_ANALYZER_COMMIT": commit},
        category="lsp",
        release_url=f"https://github.com/rust-lang/rust-analyzer/releases/tag/{tag}",
    )


def resolve_gopls() -> ToolReleaseInfo:
    repo = "https://github.com/golang/tools.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^gopls/v\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="gopls",
        version=tag,
        args={"GOPLS_VERSION": tag, "GOPLS_COMMIT": commit},
        category="lsp",
        release_url=f"https://github.com/golang/tools/releases/tag/{tag}",
    )


def resolve_clangd() -> ToolReleaseInfo:
    repo = "https://github.com/llvm/llvm-project.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^llvmorg-\d+\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="clangd",
        version=tag,
        args={"CLANGD_VERSION": tag, "CLANGD_COMMIT": commit},
        category="lsp",
        release_url=f"https://github.com/llvm/llvm-project/releases/tag/{tag}",
    )


def resolve_podman() -> ToolReleaseInfo:
    repo = "https://github.com/containers/podman.git"
    tags = get_git_tags_and_commits(repo)
    tag, commit = find_latest_tag(tags, pattern=r"^v6\.\d+\.\d+$")
    return ToolReleaseInfo(
        tool="podman",
        version=tag,
        args={"PODMAN_VERSION": tag, "PODMAN_COMMIT": commit},
        category="core",
        release_url=f"https://github.com/containers/podman/releases/tag/{tag}",
    )
