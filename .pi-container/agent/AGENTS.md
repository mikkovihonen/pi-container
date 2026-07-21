For ephemeral test runs and temporary artifacts, use `/tmp/pi_test_artifacts/`. Do not create such directories under `/workspace`.

Whenever encountering an unmet system package dependency, append the dependency into `/workspace/.pi-container/dependencies/root/commands.sh` (inside the `apt-get update && apt-get install -y` block). The system is using `apt` package management. After appending, stop and inform the user that a new dependency has been identified and a container restart is needed.

CRITICAL: Do not use the `<|tool_call>call:` syntax when explaining your reasoning or plan. Only use it at the exact moment you intend to execute a tool.