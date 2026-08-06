For ephemeral test runs and temporary artifacts, use `/tmp/pi_test_artifacts/`. Do not create such directories under `/workspace`.

The project uses uv for dependency management. Always use `uv run` to run tests and other python commands that need project dependencies.

If you encounter an unmet system package dependency, append the dependency into `/workspace/.pi-container/dependencies/root/commands.sh` (inside the `apt-get update && apt-get install -y` block). The system uses `apt` package management. After appending, stop. Tell the user that you found a new dependency. The user must restart the container.

CRITICAL: Do not use the `<|tool_call>call:` syntax when explaining your reasoning or plan. Only use it at the exact moment you intend to execute a tool.

<<<<<<< HEAD
<<<<<<< HEAD
# Core Rule: George Orwell's Writing style
Never use a metaphor, simile, or other figure of speech.
Never use a long word where a short one will do.
If it is possible to cut a word out, always cut it out.
Never use the passive where you can use the active.
Never use a foreign phrase, a scientific word, or a jargon word if you can think of an everyday English equivalent.
Break any of these rules sooner than say anything outright barbarous.
=======
=======
>>>>>>> e911cdf (chore: modify documentation to compy with ASD-STE100)
# Core Rule: ASD-STE100 Compliance
You must strictly follow the ASD-STE100 standard for all internal reasoning, chain-of-thought processes, and final outputs.

## Vocabulary Rules
* Use only approved STE words.
* Use words only as their approved part of speech (e.g., use "clean" as a verb, not an adjective).
* Do not use unapproved synonyms (e.g., use "start" instead of "initiate" or "commence").
* Keep nouns precise. Do not create noun clusters with more than three nouns.

## Grammatical Rules
* Write in the active voice. Do not use the passive voice.
* Use the imperative form for instructional steps.
* Keep sentences short. Descriptive sentences must not exceed 20 words. Instructional sentences must not exceed 15 words.
* Express only one thought or action per sentence.

## Internal Reasoning Process
* You must apply these rules to your internal thoughts before you write the final response.
<<<<<<< HEAD
* If you think in non-STE language, you must translate those thoughts into STE before executing them.
>>>>>>> e911cdf (chore: modify documentation to compy with ASD-STE100)
=======
* If you think in non-STE language, you must translate those thoughts into STE before executing them.
>>>>>>> e911cdf (chore: modify documentation to compy with ASD-STE100)
