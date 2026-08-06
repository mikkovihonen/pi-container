# Vale plugin — fix list

Findings from a review of the working-tree implementation of
[vale-prose-linting.md](vale-prose-linting.md), dated 2026-08-06.

Every claim below was reproduced with Vale 3.17.1 against the rule files and
fixtures in the working tree. Nothing here is inferred.

**Current state: the feature does not work.** Every `vale` invocation exits 2
because three rule files fail schema validation. After you fix that, the default
call still reports nothing, because the rules are all `suggestion` level and the
extension asks for `warning` and above.

Work the sections in order. Section A makes Vale run. Section B makes it report
something true. Do not start C before A and B pass their checks.

---

## A — Blocking. Nothing runs today.

### A1. Invalid keys in three rule files

Vale stops on the first rule file that fails validation, so one bad file breaks
every invocation: the tool, the command, and any manual run.

```
IngForms.yml:13     E201: has invalid keys: 'filters'
NounClusters.yml    'filters' (:10), then 'max' (:9), then 'token' (:8)
PassiveVoice.yml    'filters' (:10), then 'token' (:8)
```

`filters` is valid only on `spelling`. `existence` takes `tokens` (a list) or
`raw`, and has no `max` — that key belongs to `occurrence`.

| File | Change |
|---|---|
| [PassiveVoice.yml:8](../../pi-coding-agent/vale/styles/STE100/PassiveVoice.yml#L8) | `token: <re>` → `tokens:` list with one entry |
| [PassiveVoice.yml:10](../../pi-coding-agent/vale/styles/STE100/PassiveVoice.yml#L10) | Delete `filters:`. Use `exceptions:` if you need exclusions, or fold them into the regex. |
| [NounClusters.yml:8](../../pi-coding-agent/vale/styles/STE100/NounClusters.yml#L8) | `token:` → `tokens:` list |
| [NounClusters.yml:9](../../pi-coding-agent/vale/styles/STE100/NounClusters.yml#L9) | Delete `max: 3`. It is silently meaningless on `existence`. |
| [NounClusters.yml:10](../../pi-coding-agent/vale/styles/STE100/NounClusters.yml#L10) | Delete `filters:` |
| [IngForms.yml:13](../../pi-coding-agent/vale/styles/STE100/IngForms.yml#L13) | Delete `filters:` |

`SentenceLength.yml` is correct as written: `occurrence` does accept `token` and
`max`.

**Check:** `vale --config=<cfg> ls-config` exits 0 and every invocation of
`vale <file>` exits 0 or 1, never 2.

### A2. Default alert level hides every rule

All seven rules declare `level: suggestion`. The tool defaults `minAlertLevel`
to `"warning"`, so the default call filters out everything it could report.
Measured on a file with three real violations:

```
--minAlertLevel=suggestion → 3 alerts
--minAlertLevel=warning    → 0 alerts   ← the default
```

Pick one and apply it consistently:

- **Option 1 (recommended).** Change the extension default to `"suggestion"` at
  [index.js:254](../../pi-coding-agent/default/agent/extensions/vale/index.js#L254),
  matching `MinAlertLevel = suggestion` in
  [fallback.ini](../../pi-coding-agent/vale/fallback.ini).
- **Option 2.** Promote the rules that should block to `level: warning` and keep
  the tool default. Then say in the docs which rules are advisory.

Do not leave the extension default and the rule levels disagreeing.

**Check:** a file with a known violation produces output from a bare
`vale_lint` call with no parameters.

### A3. `pi` is out of scope in `runLint`

[index.js:117](../../pi-coding-agent/default/agent/extensions/vale/index.js#L117)
calls `pi.exec(...)`, but `pi` is a parameter of the factory at
[index.js:240](../../pi-coding-agent/default/agent/extensions/vale/index.js#L240)
and is never passed down. Every call throws `ReferenceError: pi is not defined`.

Pass `pi` into `runLint`, or move `runLint` inside the factory closure.

### A4. `vale` is passed twice as argv

[index.js:108](../../pi-coding-agent/default/agent/extensions/vale/index.js#L108)
seeds the list with the program name, and
[index.js:117](../../pi-coding-agent/default/agent/extensions/vale/index.js#L117)
passes it to `pi.exec("vale", args)`. Reproduced:

```
$ vale vale --minAlertLevel warning --output line p.md
E100 [doLint] Runtime error — argument 'vale' does not exist    exit=2
```

Start the list empty: `const args = [];`

### A5. `Vocabulary.yml` is silently dead

Two duplicate YAML keys make Vale load the file, print no error, exit 0, and
produce **zero** alerts from all 163 entries:

- `"depict"` at [lines 58 and 61](../../pi-coding-agent/vale/styles/STE100/Vocabulary.yml#L58-L61)
- `"implement"` at [lines 100 and 102](../../pi-coding-agent/vale/styles/STE100/Vocabulary.yml#L100-L102)

Deleting only those two lines makes the rule fire immediately. Nothing warns you
about this, which is why A6 matters.

### A6. `require` inside an ESM module

[index.js:220](../../pi-coding-agent/default/agent/extensions/vale/index.js#L220)
calls `require("node:fs")` in a file that uses `import` and `export default`. If
jiti does not inject `require`, the throw lands in the surrounding `catch` and
**every** path reports "Path does not exist" before Vale ever runs.

Replace `fileExists` with a top-level `import { stat } from "node:fs/promises"`
and an `await stat(...)` in a `try`.

---

## B — The output is still wrong after A.

Verified on a scratch copy with all A1 and A5 defects repaired.

### B1. `NounClusters` flags any four consecutive words

`\b\w+(?:\s+\w+){3,}\b` does not detect nouns. It matches any four words in a
row. It fires on "It is not ready" and on **six of the seven negative
fixtures**. Across [docs/](../) it produces **2547 of 3081 alerts — 83 %**.

Choose one:

- Rewrite it with `extends: upos` so it matches actual noun sequences. This is
  what the part-of-speech extension point is for.
- Delete the rule from version 1 and record the reason.

Do not ship the regex. It makes every other alert invisible.

### B2. `IngForms` fixture does not match its own rule

[ing_forms_positive.md](../../pi-coding-agent/vale/tests/ing_forms_positive.md)
reads "This is the running of the system." The rule matches `is (\w+ing)`; the
text has "is the running". The rule itself works — "The system is running."
fires. Fix the fixture.

### B3. `Contractions` misses the common cases

The swap list has `doesn't` and `didn't` but not `don't`, `it's`, `that's`,
`we're`, `you're`, `I'm`, or `let's`. Its own positive fixture contains "Don't
ship it" and goes unflagged, while
[docs/configuration.md](../configuration.md) advertises `don't`.

### B4. Negative fixtures are not isolated

`sentence_length_negative.md` contains "should not fire" and therefore trips
`Shall`. Either write fixtures that trip exactly one rule, or run each fixture
with `--filter='.Name == "STE100.<Rule>"'`.

### B5. Wrong and harmful vocabulary mappings

These are technical terms in this repo and the replacement damages meaning:
`procedure`→`step`, `protocol`→`rule`, `resource`→`tool`, `function`→`job`,
`option`→`choice`, `require`→`need`.

These glosses are simply wrong: `advantage`→`good`, `amazing`→`good`,
`captivate`→`hold`, `phoneme`→`sound`, `plagiarize`→`copy`.

`Shall.yml` maps `should`→`must`, which turns a recommendation into a
requirement, and `may`→`can` with `ignorecase: true`, so it also flags the month
"May".

### B6. Answer open question 1 before keeping `Vocabulary.yml`

[vale-prose-linting.md](vale-prose-linting.md) asks whether this repo may
redistribute a word list derived from ASD-STE100. The file ships 163 such pairs
and the question is still open.

---

## C — Container and build

### C1. The styles COPY busts the whole layer cache

[Containerfile:128](../../pi-coding-agent/Containerfile#L128) copies
`pi-coding-agent/vale/` before the toolchain COPYs (line 143), `npm install -g`
(line 324), and the CA-cert steps. Editing one YAML rule invalidates every layer
after it — the full Python, Node and podman staging plus the npm install. That
is the exact loop you are in while you develop rules.

Move the `COPY` to the end of the file. The pinned binary download at line 102
can stay where it is; it changes only on a version bump.

### C2. `entrypoint.sh` can now stop the container from starting

The new [lines 62-70](../../pi-coding-agent/entrypoint.sh#L62-L70) run
unconditionally under `set -e`, so a failed `install -d` or `ln -sfn` kills
startup. A prose linter must not be able to take down the agent. Match the file's
own convention:

```bash
install -d -o pi -g pi /home/pi/.config/vale 2>/dev/null \
  && ln -sfn /usr/local/share/vale/fallback.ini /home/pi/.config/vale/.vale.ini \
  || echo "WARNING: could not install the Vale fallback config"
```

### C3. `TARGETARCH` is never passed explicitly

[build.py](../../src/build.py) does not pass it. Podman's automatic platform args
should populate it, and `vale --version` fails loudly at build time if they do
not. Add a comment recording the dependency, or pass `--build-arg` outright.

**Verified sound, do not change:** gosu unsets `HOME` so `SetupUser` repopulates
it from the passwd entry. The pi shell gets `/home/pi`, and Go's xdg resolves
`/home/pi/.config/vale/.vale.ini`. The D3 symlink mechanism works.

---

## D — Extension code quality

| # | Location | Problem | Fix |
|---|---|---|---|
| D1 | [index.js:43-53](../../pi-coding-agent/default/agent/extensions/vale/index.js#L43-L53) | The regex does not match Vale's real `--output=line` format, which is `p.md:1:4:STE100.Contractions:message` — no `[severity]`, and `:` directly after the column. `(\S+)` swallows `STE100.Contractions:Do`, fusing the check name with the first word of the message. | Split on the first four `:`, or switch to `--output=JSON` |
| D2 | [index.js:51](../../pi-coding-agent/default/agent/extensions/vale/index.js#L51) | Every alert is stamped `[warning]`. The `line` format carries no severity, so this value is invented. | Drop it, or read severity from JSON output |
| D3 | [index.js:210](../../pi-coding-agent/default/agent/extensions/vale/index.js#L210) | `filesScanned: lines.length` is the alert count, not a file count | Count distinct paths |
| D4 | [index.js:92](../../pi-coding-agent/default/agent/extensions/vale/index.js#L92) | `new URL().pathname` returns a percent-encoded path, so any path with a space fails the existence check | `path.resolve(ctx.cwd, userPath)` |
| D5 | [index.js:90](../../pi-coding-agent/default/agent/extensions/vale/index.js#L90) | `basePath` is computed and never used | Delete |
| D6 | [index.js:232](../../pi-coding-agent/default/agent/extensions/vale/index.js#L232) | `buildStatus` takes `filesScanned` and ignores it | Use it or drop the parameter |
| D7 | [index.js:265](../../pi-coding-agent/default/agent/extensions/vale/index.js#L265) | `/vale` returns silently when `!ctx.hasUI`; the user gets nothing in print and JSON modes | Print the result instead |
| D8 | [index.js:248](../../pi-coding-agent/default/agent/extensions/vale/index.js#L248) and `AGENTS.md` | Both carry the ASD-STE100 instruction to the model. Open question 4 in the plan, still unanswered. | Pick one owner |

---

## E — Process and housekeeping

### E1. Nothing executes the fixtures

`pi-coding-agent/vale/tests/` holds 14 fixtures and no runner: no script, no
pytest case, no CI hook. Running Vale once against one fixture would have caught
A1, A5 and B2 in seconds.

**Do this before any other fix in this document.** A test that maps each fixture
to the rule names it must and must not produce turns every finding in sections A
and B into a red test.

### E2. The plan's acceptance gates were skipped

[vale-prose-linting.md](vale-prose-linting.md) sets an order: get
`vale --version`, `vale ls-config` and `vale README.md` working in the container
**before writing any JavaScript**, and gate step 7 on "every rule fires on its
positive fixture and stays quiet on its negative one". Neither gate ran.

### E3. `_ensure_project_config` now resurrects deleted files

The per-file walk at [run.py:152-166](../../src/run.py#L152-L166) copies any
missing template file under `agent/` on every launch. Delete
`extensions/vale/index.js` to turn the extension off, or `auth.json` to force
re-auth, and it returns on the next run. The plan asked for "never overwrite"; it
did not consider "never resurrect".

Limit the walk to a known list of template subpaths, or document the behavior.

### E4. `_compute_image_hash` is correct but hard to read

[run.py:243-289](../../src/run.py#L243-L289) mixes plain strings and 3-tuples in
one list, sorts them through a type-testing lambda, and carries a `"dir"` marker
and a `_dir_name` element that nothing reads. A list of `(sort_key,
absolute_path)` pairs does the same work in half the lines. The tests pass;
this is a readability fix, not a defect.

### E5. The CHANGELOG entry is not release-body text

[CHANGELOG.md](../../CHANGELOG.md) gained one six-sentence paragraph that names
`_compute_image_hash()` and `_ensure_project_config()`. Commit `55b6a3f` made
this file the GitHub release body. Cut it to what a user sees: a new `vale_lint`
tool, a new `/vale` command, and Vale in the image.

### E6. `docs/configuration.md` documents behavior that does not exist

[docs/configuration.md](../configuration.md) claims `Contractions` checks
`don't` (it does not — B3) and that `IngForms` suggests `is running` → `runs`
(the rule suggests "does running"). Correct these when the rules are fixed, not
before.

---

## Verification gate

The work is done when all of these pass:

1. `vale --version` inside the container prints 3.17.1.
2. `vale ls-config` lists all seven rules and exits 0.
3. Every fixture in `pi-coding-agent/vale/tests/` produces exactly the rule names
   it is named for, and no others — asserted by a test, not by eye.
4. A bare `vale_lint` call with no parameters reports the violations in a file
   that has them.
5. `/vale` shows the same result as `vale_lint` on the same path.
6. `pytest src/tests/` passes.
7. Linting [docs/](../) produces a count a person would read. Today, with every
   schema error repaired, it is 3081 alerts over 17 files.
