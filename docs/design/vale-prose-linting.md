# vale.sh Prose Linting — Design

**Status:** ready to implement. Revision 2.
**Supersedes:** revision 1, which was implemented and then removed. See
[What revision 1 got wrong](#what-revision-1-got-wrong).

This design adds prose linting to the agent through the `vale` CLI. The agent
gets a `vale_lint` tool and a `/vale` command. Both run only when called.

Every external fact in this document is verified against these sources:

| Source | Version | Used for |
|---|---|---|
| `errata-ai/vale` Go source | v3.17.1 | CLI flags, exit codes, output formats, config search |
| `@earendil-works/pi-coding-agent` npm package | 0.84.0 | extension loader, `pi.exec`, tool signature |
| `errata-ai/packages` library index | fetched 2026-08-06 | available style packages |
| `src/run.py`, `pi-coding-agent/Containerfile` | this repo, `main` | mounts, seeding, image hash |

Do not change the code paths below on memory of how Vale works. Re-read the
cited source first.

---

## Problem

The agent writes prose: docs, guides, READMEs, commit messages. The repo
requires ASD-STE100 (see `pi-coding-agent/default/agent/AGENTS.md` and commit
`e911cdf`). Today nothing checks that requirement. The model gets no signal, so
it cannot correct itself.

---

## What revision 1 got wrong

Revision 1 shipped code that could not work. Each row below is a defect that
this revision removes by design, not by a fix.

| Defect | Cause |
|---|---|
| The default call never ran Vale | The tool returned early when it found no `vale.ini` and `enforceSte` was true. `enforceSte` was the default. |
| No rules existed | The design declared the STE rule files "design reference only. NOT shipped". Vale had nothing to enforce. |
| `--fix` always failed | Vale has no `--fix` flag. `vale fix` is a subcommand that repairs one serialized alert. |
| SARIF output was empty | Vale has no SARIF output. The parser read `runs[0].results`; Vale emits `{"path": [Alert]}`. |
| Warnings were reported as "no violations" | Vale exits 0 unless an **error**-severity alert exists. The code read exit 0 as empty output. |
| `minAlertLevel` had no effect | The flag was passed only when it differed from `"warning"`, so the documented default never reached Vale. |
| An object was printed as text | `formatSarifSummary` returned an object; the caller put it in a `text` field. |
| Edits to the extension did not reach the container | The extension was copied into the image, but `_compute_image_hash` hashes only `Containerfile` and `entrypoint.sh`. |
| The build patched a third-party bundle | A string replace against `dist/extensions/index.js`, unpinned, with no assertion that the replace matched. |

The root cause is the same in every row: the design specified behavior that
nobody checked against the tools. This revision states the contract first.

---

## Ground truth

### Vale CLI contract

From `cmd/vale/flag.go`, `cmd/vale/main.go`, `cmd/vale/command.go`,
`cmd/vale/json.go`, `internal/core/config.go`, `internal/check/filter.go`.

**Flags.** `--config`, `--filter`, `--glob`, `--minAlertLevel`, `--output`,
`--ext`, `--path`, `--sources`, `--no-exit`, `--no-wrap`, `--no-global`,
`--ignore-syntax`, `--mode-compat`, `--sort`, `--normalize`, `--relative`,
`--version`, `--help`. **There is no `--fix`.**

**Subcommands.** `sync`, `ls-config`, `ls-metrics`, `ls-dirs`, `ls-vars`, `fix`,
and several private ones. `vale fix` repairs a single serialized alert for
editor integrations. It does not take a path.

**`--output`** accepts `CLI` (default), `line`, `JSON`, or a path to a Go
template. **There is no SARIF mode.**

**Exit codes.**

| Code | Meaning |
|---|---|
| 0 | Vale ran. No **error**-severity alert exists. Suggestions and warnings can still be in stdout. |
| 1 | Vale ran. At least one error-severity alert exists. |
| 2 | Vale failed: bad flag, missing config, unreadable path. |

Only `Severity == "error"` sets the nonzero code (`cmd/vale/color.go:69`,
`line.go:33`, `json.go:15`). **Never treat exit 0 as empty output.**

**JSON output** is `map[path][]Alert`. Each `Alert` has `Action{Name, Params}`,
`Span[begin, end]`, `Check`, `Description`, `Link`, `Message`, `Severity`,
`Match`, `Line` (`internal/core/alert.go`).

**Config search.** Vale looks for `.vale`, `_vale`, `vale.ini`, `.vale.ini`, and
`_vale.ini`, from the target upward. The documented name is `.vale.ini`. If it
finds no project config, it falls back to the global config at
`$XDG_CONFIG_HOME/vale/.vale.ini`. If neither exists, Vale raises E100 and exits
2. `VALE_CONFIG_PATH` replaces the whole search; `VALE_STYLES_PATH` sets the
default `StylesPath` only.

**`--filter`** takes an `expr-lang` expression over the rule fields `.Name`,
`.Level`, `.Scope`, `.Message`, `.Description`, `.Extends`, `.Link`. Example:
`--filter='.Name matches "^STE100"'`. This is how to show STE alerts only.
Do not grep the output.

**`vale sync`** downloads `Packages` entries. Styles that already live on the
`StylesPath` need no sync and no network.

### pi extension contract

From `dist/core/extensions/loader.js`, `dist/core/exec.d.ts`,
`dist/core/extensions/types.d.ts`, `dist/config.js`, `docs/extensions.md` in
`@earendil-works/pi-coding-agent@0.84.0`.

pi discovers extensions in two directories and needs no patching:

1. Global: `getAgentDir()/extensions/` → `/home/pi/.pi/agent/extensions/`
2. Project: `<cwd>/.pi/extensions/`

Each can hold `<name>.js`, `<name>/index.js`, or a directory with a
`package.json` that declares a `pi.extensions` field. `pi -e <path>` loads one
for a single run. Only the discovered directories support `/reload`.

`typebox` 1.3.7 is a dependency of the package and the loader exposes it to
extensions as a virtual module, so `import { Type } from "typebox"` resolves.
`Type.StringEnum` does not appear anywhere in the package; use
`Type.Union([Type.Literal(...), ...])` unless you first confirm `StringEnum`
exists in typebox 1.3.7.

`pi.exec(command, args, options?)` returns `{stdout, stderr, code, killed}`.
`options` is `{signal?, timeout?, cwd?}`. Use `killed` to tell cancellation from
failure.

Tool shape: `execute(toolCallId, params, signal, onUpdate, ctx)`.
`ctx` provides `cwd`, `mode`, `hasUI`, `signal`, and `ui`.

### Container contract

From `src/run.py` and `pi-coding-agent/Containerfile`.

- `/home/pi/` is a **tmpfs** (`run.py:1451`). Anything the image writes below it
  disappears at run time.
- `{PROJECT_DIR}/.pi-container/agent` is bind-mounted onto `/home/pi/.pi/agent`
  (`run.py:1453`). This is pi's global extension directory inside the container.
- `_ensure_project_config` (`run.py:126`) seeds `.pi-container/agent` from
  `pi-coding-agent/default/agent/` **only when the whole directory is absent**.
  Existing workspaces get nothing new.
- `_compute_image_hash` (`run.py:195`) hashes `Containerfile` and
  `entrypoint.sh` only. Any other file baked into the image is invisible to
  cache invalidation.
- The allowlist (`pi-coding-agent/default/allowlist.yaml`) permits `github.com`
  and `*.githubusercontent.com`, so a run-time download would pass the proxy.
  The build itself runs on the host and does not use the proxy.

---

## Decisions

### D1 — Deliver the extension through the seeded agent directory

Put the extension at `pi-coding-agent/default/agent/extensions/vale/index.js`.
`_ensure_project_config` copies it to `.pi-container/agent/extensions/vale/`,
and the bind mount puts it at `/home/pi/.pi/agent/extensions/vale/` where pi
discovers it.

This removes the `dist` patch, the patch script, the npm layout dependency, and
the image-hash problem for the extension code. Users can edit or delete their
copy per project, and `/reload` works.

**Required change:** `_ensure_project_config` must also seed template paths that
appear under an existing `agent/` directory. Today it skips the whole subtree
when `agent/` exists, so every current workspace would miss the extension. Seed
per relative path: walk `pi-coding-agent/default/agent/`, and copy each file
that has no counterpart in the project. Never overwrite an existing file.

Do not bake the extension into `/home/pi/...` in the image. The tmpfs and the
bind mount both mask it.

### D2 — Install Vale from the pinned official release

Do not use `pip install vale`. That PyPI package is an unaffiliated wrapper that
downloads the Go binary **on first execution**, as root at build time, into a
cache the `pi` user may not be able to read. It turns a build-time dependency
into a run-time network call.

Download the official release in the Containerfile, verify the checksum, and
select the asset by architecture. The image runs `linux/arm64` on Apple Silicon
hosts, so both assets are required.

```dockerfile
ARG VALE_VERSION=3.17.1
ARG TARGETARCH
# sha256 from vale_${VALE_VERSION}_checksums.txt on the release page.
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) asset="Linux_64-bit"; sum="db947f89f2292e6a0381a61de155f6a5f5cb4cb460ca178ea412ef605559cefd" ;; \
      arm64) asset="Linux_arm64";  sum="92d91ebf9ee69ec077379be95cd09e6710ab33d3d5bab66bb482e66ebc80dc23" ;; \
      *) echo "unsupported arch: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/vale.tgz \
      "https://github.com/errata-ai/vale/releases/download/v${VALE_VERSION}/vale_${VALE_VERSION}_${asset}.tar.gz"; \
    echo "${sum}  /tmp/vale.tgz" | sha256sum -c -; \
    tar -xzf /tmp/vale.tgz -C /usr/local/bin vale; \
    rm /tmp/vale.tgz; \
    vale --version
```

Every step fails the build on error. No step hides a failure behind `|| echo`.

### D3 — Ship a global Vale config so no project needs one

Vale exits 2 when it finds no config. The image must therefore provide a
fallback that a project config can still override.

- Styles go to `/usr/local/share/vale/styles/` in the image. Root-owned and
  read-only for the agent.
- The fallback config goes to `/usr/local/share/vale/fallback.ini`.
- `pi-coding-agent/entrypoint.sh` links it into the XDG location before it
  starts pi, because `/home/pi` is a tmpfs and the image cannot pre-create it:

  ```bash
  install -d -o pi -g pi /home/pi/.config/vale
  ln -sfn /usr/local/share/vale/fallback.ini /home/pi/.config/vale/.vale.ini
  ```

Vale then resolves configs by its own rules: a project `.vale.ini` wins, the
fallback applies otherwise, and `--no-global` skips the fallback. **The
extension performs no config discovery of its own.** Delete that idea; it caused
two of the revision 1 defects.

`fallback.ini`:

```ini
StylesPath = /usr/local/share/vale/styles
MinAlertLevel = suggestion

[*.{md,txt,adoc}]
BasedOnStyles = STE100
```

`Vale.Spelling` is deliberately absent. This repo's prose is full of identifiers
and command names, and the built-in spell check reports them as errors.
Add it later behind a vocabulary file if someone wants it.

### D4 — The STE style must be written, not imported

The official Vale package library holds 16 packages: AsciiDoc, Elastic, Google,
Hugo, Joblint, MDX, Microsoft, NoAnimalViolence, OpenShiftAsciiDoc, Readability,
RedHat, Salesforce, alex, neighbor, proselint, write-good. **None implements
ASD-STE100.** The community repos that claim to (`aldair-torres/ste100-vale-rules`,
`Syntaf/vale-llm-slop`) have almost no users and no release process. Do not
depend on them.

ASD-STE100 has about 65 writing rules and a controlled dictionary of about 900
approved words. The dictionary belongs to ASD, and this repo must not bundle it
before someone checks the terms. **Resolve that question before you write a
vocabulary rule** (see [Open questions](#open-questions)).

Scope version 1 to rules that need no dictionary. Each rule is one file in
`pi-coding-agent/vale/styles/STE100/`:

| Rule | Vale extension point | STE basis |
|---|---|---|
| `SentenceLength.yml` | `metric` or `occurrence` | 20 words in a procedure, 25 in a description |
| `PassiveVoice.yml` | `existence` with `upos`-based refinement | Use the active voice |
| `Contractions.yml` | `substitution` | Do not use contractions |
| `NounClusters.yml` | `existence` | No more than three nouns in a row |
| `Shall.yml` | `substitution` | `shall`/`should`/`may` → `must`/`can` |
| `IngForms.yml` | `existence` | Avoid the `-ing` form as a verb |
| `Vocabulary.yml` | `substitution` | Hand-written list of common non-approved words |

Available extension points, from `internal/check/`: `existence`,
`substitution`, `occurrence`, `consistency`, `conditional`, `capitalization`,
`readability`, `repetition`, `sequence`, `spelling`, `metric`, `script`, `upos`,
`matchcase`, `anchor`.

Each rule needs a fixture: a short Markdown file that must trigger it and a
short one that must not. `vale ls-config` must list every rule. A rule that
matches nothing is worse than no rule, because it makes clean output a lie.

### D5 — Make the image hash cover what the image contains

`/usr/local/share/vale/styles/` comes from `pi-coding-agent/vale/`. Change
`_compute_image_hash` to hash that tree as well, or a rule edit will never reach
a rebuilt image. Suggested shape:

```python
_IMAGE_DEFINITION_FILES = ("Containerfile", "entrypoint.sh")
_IMAGE_DEFINITION_DIRS = ("vale",)   # relative to pi-coding-agent/
```

Walk each directory, sort the paths, and hash file contents in that order. The
docstring at `run.py:196` lists the hashed inputs and must be updated with them.

### D6 — Cut from version 1

| Cut | Reason |
|---|---|
| `applyFixes` / `--fix` | The flag does not exist. |
| `outputFormat: "sarif"` | The format does not exist. |
| `enforceSte` | It described behavior nothing implemented. The fallback config makes STE the default already. |
| Config discovery in the extension | Vale does it, and does it right. |
| `getArgumentCompletions` | Revision 1 returned `null` from it. Omit it, or implement it. |

---

## Tool: `vale_lint`

| Field | Value |
|---|---|
| `name` | `vale_lint` |
| `label` | `Vale Lint` |
| `description` | Run Vale prose linting on a file or directory. Reports ASD-STE100 problems. |
| `promptSnippet` | Run prose linting with Vale |
| `promptGuidelines` | Write in ASD-STE100. Use approved words. Use the active voice. Keep sentences short. |

`AGENTS.md` already carries an STE instruction. Decide which one owns the rule
and remove the duplicate, or the model reads two overlapping instructions.

**Parameters**

| Name | Type | Default | Notes |
|---|---|---|---|
| `path` | string | `ctx.cwd` | Resolve against `ctx.cwd` before you check that it exists. |
| `minAlertLevel` | `"suggestion" \| "warning" \| "error"` | `"warning"` | Always pass `--minAlertLevel`. |
| `outputFormat` | `"text" \| "json"` | `"text"` | `text` → `--output=line`; `json` → `--output=JSON`. |
| `steOnly` | boolean | `false` | Adds `--filter='.Name matches "^STE100"'`. |

Use `--output=line` and not the default `CLI` format for `text`. The `CLI`
format is a wrapped table with per-file headings and a summary footer, which is
why revision 1 counted violations wrongly. The `line` format is one alert per
line: `path:line:col: Check message`.

**Execution**

1. Resolve `path` against `ctx.cwd`. Return a clear error when it does not
   exist. Do not call `access` on an unresolved relative path.
2. Build the argument list. Always include `--minAlertLevel` and `--output`.
3. `pi.exec("vale", args, { cwd: ctx.cwd, signal })`.
4. Branch on the exit code, and use stdout in every branch:

| Result | Return |
|---|---|
| `killed === true` | Cancelled. `isError: false`. |
| `code === 0`, stdout empty | "No problems found." `violationCount: 0`. |
| `code === 0`, stdout not empty | Alerts below error level. Report them. `isError: false`. |
| `code === 1` | Alerts, at least one error. Report them. `isError: false`. |
| `code >= 2` | Failure. `isError: true`. Include stderr and the argument list. |

5. Count alerts from the parsed data, never from a line count of the `CLI`
   format. For `json`, parse `map[path][]Alert` and count entries. For `text`,
   count lines only because `--output=line` guarantees one alert per line.

**Return**

```js
{
  content: [{ type: "text", text: formatted }],   // a string, always
  details: { isError, violationCount, filesScanned, bySeverity, raw }
}
```

`raw` holds the unparsed JSON when `outputFormat` is `json`. Set `raw` to
`undefined` otherwise. Never place an object where a string belongs.

---

## Command: `/vale`

| Argument | Effect |
|---|---|
| `/vale` | Lint `ctx.cwd` at warning level |
| `/vale docs/` | Lint one directory |
| `/vale README.md` | Lint one file |
| `/vale --minAlertLevel=suggestion` | Lower the level |
| `/vale --ste-only` | Add the `STE100` rule filter |

The command must call the same code path as the tool. Revision 1 had two
implementations that disagreed: the command passed no `--config` and no signal.
Write one function, and have both entry points call it.

Guard on `ctx.hasUI`, not on `ctx.mode === "tui"`, unless the command truly needs
the TUI. Pass `ctx.signal` so Esc cancels the child process. Show a count with
`ctx.ui.setStatus("vale", ...)`. Long output does not belong in `ctx.ui.notify`;
show a summary and the first N alerts, and tell the user to call the tool for
the rest.

---

## Errors

| Case | Behavior |
|---|---|
| `vale` not on PATH | `pi.exec` rejects or returns a nonzero code. Report that the image is built wrong, and name the Containerfile step. Do not tell the user to `pip install vale`; the image owns this binary. |
| Path does not exist | Report the resolved absolute path. |
| Exit 2 with "no .vale.ini" | The fallback link is missing. Report the entrypoint step from D3. |
| Exit 2, other | Report stderr and the exact arguments. |
| Cancelled | `killed === true`. Report the cancellation, not a failure. |
| JSON parse fails | Report the parse failure and return the raw output. Do not swallow the error and report zero alerts. |

---

## Implementation order

Each step has an acceptance check. Do not start a step before the previous
check passes.

| # | Step | Acceptance check |
|---|---|---|
| 1 | Add the Vale install to `Containerfile` (D2) | `podman run <image> vale --version` prints 3.17.1 |
| 2 | Add `pi-coding-agent/vale/` with `fallback.ini` and one real rule | `vale ls-config` in the container lists the rule |
| 3 | Link the fallback config in `entrypoint.sh` (D3) | `vale README.md` inside the container exits 0 or 1, never 2 |
| 4 | Extend `_compute_image_hash` (D5) plus a unit test | Editing a rule file changes the hash; `pytest src/tests/test_run.py` passes |
| 5 | Extend `_ensure_project_config` to seed new template paths (D1) plus a unit test | A workspace with an existing `.pi-container/agent/` gains `extensions/vale/` on the next run |
| 6 | Write the extension at `pi-coding-agent/default/agent/extensions/vale/index.js` | pi's startup Extensions list shows `vale`; `/vale` runs |
| 7 | Write the remaining STE rules with fixtures (D4) | Every rule fires on its positive fixture and stays quiet on its negative one |
| 8 | Update `CHANGELOG.md` and `docs/configuration.md` | The release body mentions the tool and the new image dependency |

Steps 1 to 3 deliver a working `vale` in the container with no extension at all.
That is the checkpoint that revision 1 never reached. Verify it by hand before
you write any JavaScript.

---

## Tests

| Test | Location | Asserts |
|---|---|---|
| Image hash covers rule files | `src/tests/test_run.py` | Editing `pi-coding-agent/vale/styles/STE100/*.yml` changes `_compute_image_hash` |
| Seeding fills an existing agent dir | `src/tests/test_run.py` | `_ensure_project_config` adds `extensions/vale/index.js` to a pre-existing `.pi-container/agent/` and overwrites nothing |
| Rule fixtures | `pi-coding-agent/vale/tests/` | `vale --config=... <fixture>` produces the expected alert names |
| Exit code 0 with output | rule fixture | A warning-only file gives exit 0 and non-empty stdout, and the extension reports the alerts |
| Container smoke test | manual, recorded in `docs/development.md` | `vale --version`, `vale ls-config`, and `/vale README.md` all work in a fresh container |

The extension itself has no test harness in this repo. Keep the logic small
enough to check by reading it, and put every rule that can be wrong into the
Vale fixtures instead.

---

## Open questions

Answer these before step 4. Each changes what gets built.

1. **ASD-STE100 dictionary terms.** May this repo redistribute a word list
   derived from the standard? If not, `Vocabulary.yml` must hold only words
   written from scratch, and the design must say so.
2. **Scope of the fallback config.** `[*.{md,txt,adoc}]` lints Markdown and text.
   Should it also lint code comments through Vale's comment scopes? That is a
   large jump in alert volume.
3. **Sentence-length limits.** ASD-STE100 sets 20 words for a procedural
   sentence and 25 for a descriptive one. Vale cannot tell the two apart. Pick
   one limit for all prose, or split the rule by file scope, and record why.
4. **Who owns the STE instruction to the model:** `AGENTS.md` or the tool's
   `promptGuidelines`? Two copies will drift.
5. **Version bumps.** `VALE_VERSION` is pinned by checksum. Who updates it, and
   does the repo already have a pattern for pinned downloads to follow?

---

## Out of scope

- Automatic fixes. Vale styles can carry an `Action`, and `vale fix` can apply
  one, but only per alert through an editor protocol. Revisit when someone needs
  it.
- Linting on every turn. A `before_agent_start` hook adds latency and noise.
- Vale packages from the network. Local styles need no `vale sync`, and a
  run-time download is a new failure mode inside the proxy.
- Publishing the STE style as its own Vale package. Worth doing once the rules
  prove themselves, not before.
