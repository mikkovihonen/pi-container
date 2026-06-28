edit tool requires BOTH path AND content.

For ephemeral test runs and temporary artifacts, use `/tmp/pi_test_artifacts/`. Do not create such directories under `/workspace`.

Whenever encountering an unmet system package dependency, the correct course of action is to append the dependency into `/workspace/.pi/dependencies/apt/packages.txt`. The system is using `apt` package management. After appending, the agent must stop and inform the user that a new dependency has been identified.

When trying to understand workspace structure, use `git ls-files --cached --others --exclude-standard | tree --fromfile - --noreport` if the workspace is a git repository or use `fdfind` as a fallback option. If you can't find the file user is referring to with those two commands, stop and ask the user if other means should be used instead.