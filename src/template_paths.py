from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _resolve_chat_template_path(
    template_path: str,
    models_path: Path,
    seed_dir: Path | None = None,
) -> Path:
    """Resolve a ``--chat-template-file`` path to an absolute path on disk.

    Handles three cases:

    1. ``.pi-container/...`` — resolves relative to the seed directory (if
       provided) or the project's ``.pi-container`` (for seeded configs),
       or the repo's ``pi-coding-agent/default/`` (for seed templates).
    2. ``pi-coding-agent/default/...`` — resolves relative to the seed directory.
    3. Any other path — resolves relative to the models.json parent directory.
    """
    if template_path.startswith(".pi-container/"):
        suffix = template_path[len(".pi-container/") :]
        if seed_dir is not None:
            return seed_dir / suffix
        # Seeded config: .pi-container is grandparent of models.json
        if models_path.parent.name == "agent" and "pi-coding-agent" in str(models_path):
            # Seed template: models.json is at pi-coding-agent/default/agent/models.json
            return models_path.parent.parent.parent / "default" / suffix
        return models_path.parent.parent / suffix

    if seed_dir is not None and template_path.startswith("pi-coding-agent/default/"):
        return seed_dir / template_path[len("pi-coding-agent/default/") :]

    return models_path.parent / template_path


def _check_chat_template_paths(
    data: dict,
    models_path: Path,
    errors: list[str],
    seed_dir: Path | None = None,
) -> None:
    """Check that ``--chat-template-file`` paths exist on disk.

    For seed templates, resolves ``.pi-container`` → ``pi-coding-agent/default/``.
    For seeded configs, resolves ``.pi-container`` from the project root.
    """
    providers = data.get("providers", {})
    for provider_name, provider_cfg in providers.items():
        server_params = provider_cfg.get("serverCustomParameters", {})
        flags = server_params.get("flags", [])
        if not isinstance(flags, list):
            continue

        for i, flag in enumerate(flags):
            if flag == "--chat-template-file":
                if i + 1 >= len(flags):
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file has no following path"
                    )
                    continue

                template_path = flags[i + 1]
                if not isinstance(template_path, str):
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file path is not a string"
                    )
                    continue

                resolved = _resolve_chat_template_path(template_path, models_path, seed_dir)

                if not resolved.exists():
                    errors.append(
                        f"  providers.{provider_name}.serverCustomParameters.flags[{i}] "
                        f"--chat-template-file path does not exist: {resolved}"
                    )
