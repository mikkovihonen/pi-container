For ephemeral test runs and temporary artifacts, use `/tmp/pi_test_artifacts/`. Do not create such directories under `/workspace`.

Whenever encountering an unmet system package dependency, the correct course of action is to append the dependency into `/workspace/dependencies/apt/packages.txt`. The system is using `apt` package management. After appending, the agent must stop and inform the user that a new dependency has been identified.
