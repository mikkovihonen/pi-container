For ephemeral test runs and temporary artifacts, use `/tmp/pi_test_artifacts/`. Do not create such directories under `/workspace`.

The project uses uv for dependency management. Always use `uv run` to run tests and other python commands that need project dependencies.

If you encounter an unmet system package dependency, append the dependency into `/workspace/.pi-container/dependencies/root/commands.sh` (inside the `apt-get update && apt-get install -y` block). The system uses `apt` package management. After appending, stop. Tell the user that you found a new dependency. The user must restart the container.

CRITICAL: Do not use the `<|tool_call>call:` syntax when explaining your reasoning or plan. Only use it at the exact moment you intend to execute a tool.

# Core Rule: George Orwell's Writing style
Never use a metaphor, simile, or other figure of speech.
Never use a long word where a short one will do.
If it is possible to cut a word out, always cut it out.
Never use the passive where you can use the active.
Never use a foreign phrase, a scientific word, or a jargon word if you can think of an everyday English equivalent.
Break any of these rules sooner than say anything outright barbarous.