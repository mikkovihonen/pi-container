/**
 * Prose linting extension.
 *
 * Provides:
 * - Tool: `prose_lint` — run Vale + spaCy on a file or directory.
 * - Command: `/prose-lint [path] [--minAlertLevel=suggestion|warning|error] [--ste-only] [--spacy]`
 *
 * Both entry points share one implementation so they cannot disagree.
 *
 * Combines:
 * - Vale CLI for general prose checks (spelling, capitalization, etc.)
 * - spaCy for NLP-powered ASD-STE100 checks (contractions, passive voice, etc.)
 *
 * Vale contract (errata-ai/vale v3.17.1):
 *   --output=line   one alert per line: "path:line:col:CheckName:message"
 *   --output=JSON   map[path][]Alert JSON
 *   exit 0  → no error-severity alert (suggestions and warnings still appear)
 *   exit 1  → at least one error-severity alert
 *   exit 2  → Vale failed (bad flag, missing config, unreadable path)
 *   There is NO --fix flag and NO SARIF output mode.
 */

import { stat } from "node:fs/promises";
import { resolve as pathResolve } from "node:path";
import { Type } from "typebox";

const LintParams = Type.Object({
	path: Type.Optional(
		Type.String({ description: "File or directory to lint. Resolved against ctx.cwd." }),
	),
	minAlertLevel: Type.Optional(
		Type.Union([
			Type.Literal("suggestion"),
			Type.Literal("warning"),
			Type.Literal("error"),
		]),
	),
	outputFormat: Type.Optional(
		Type.Union([Type.Literal("text"), Type.Literal("json")]),
	),
	steOnly: Type.Optional(Type.Boolean()),
	spacy: Type.Optional(Type.Boolean({ description: "Enable ASD-STE100 checks via spaCy." })),
});

/**
 * Parse one alert from Vale's `--output=line` form.
 *
 * Line shape: `<path>:<line>:<col>:<CheckName>:<message>`
 * No severity in this format — it is always suggestion level unless the rule
 * declares a higher level.
 *
 * Split on the first four colons so the message (which may contain colons)
 * survives intact, and so the check name (e.g. `STE100.Contractions`) is not
 * fused with the first word of the message.
 */
function formatLineAlert(line) {
	const parts = line.split(":");
	if (parts.length < 5) {
		return line;
	}
	const path = parts[0];
	const lineNo = parts[1];
	const col = parts[2];
	const check = parts[3];
	const message = parts.slice(4).join(":");
	return `${path}:${lineNo}:${col} ${check}: ${message}`;
}

/**
 * Count alerts from parsed JSON output.
 *
 * Vale JSON is map[path][]Alert. Every entry in every array is one alert.
 */
function countAlertsFromJson(data) {
	let total = 0;
	const bySeverity = {};
	if (data && typeof data === "object" && !Array.isArray(data)) {
		for (const _path of Object.keys(data)) {
			const alerts = data[_path];
			if (Array.isArray(alerts)) {
				for (const alert of alerts) {
					total++;
					const sev = alert.Severity ?? "suggestion";
					bySeverity[sev] = (bySeverity[sev] ?? 0) + 1;
				}
			}
		}
	}
	return { total, bySeverity };
}

/**
 * The single shared implementation. Both the tool and the command call it.
 *
 * @param {object} pi  pi extension API
 * @param {string} userPath  User-supplied path or undefined.
 * @param {"suggestion"|"warning"|"error"} minAlertLevel
 * @param {"text"|"json"} outputFormat
 * @param {boolean} steOnly
 * @param {boolean} spacy  Enable ASD-STE100 checks via spaCy.
 * @param {object} ctx  ExtensionContext: {cwd, mode, hasUI, signal, ui}
 * @returns {{content, details}}
 */
async function runLint(pi, userPath, minAlertLevel, outputFormat, steOnly, spacy, ctx) {
	// 1. Resolve path against ctx.cwd.
	const resolved = userPath
		? pathResolve(ctx.cwd, userPath)
		: ctx.cwd;

	let statResult;
	try {
		statResult = await stat(resolved);
	} catch {
		return {
			content: [
				{
					type: "text",
					text: `Path does not exist: ${resolved}`,
				},
			],
			details: { isError: true, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined },
		};
	}

	// 2. Build the argument list. Always include --minAlertLevel and --output.
	const args = [];
	args.push("--minAlertLevel", minAlertLevel);
	args.push("--output", outputFormat === "json" ? "JSON" : "line");
	if (steOnly) {
		args.push("--filter", '.Name matches "^STE100"');
	}

	// Track file paths separately so we can retry without failed files.
	const filePaths = [];

	// When scanning a directory, walk it to find prose files, skipping
	// non-prose directories like .venv/, node_modules/, etc. which may
	// contain markdown files with invalid YAML frontmatter that causes
	// Vale to emit E201 errors.
	const nonProseDirs = new Set([
		".git", ".venv", "venv", "node_modules", "__pycache__",
		".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
		"dist", "build", "*.egg-info", "archives", "tmp", "temp",
	]);
	if (statResult.isDirectory()) {
		// Walk the directory tree and collect prose files.
		const files = [];
		async function walk(dir) {
			const { readdir } = await import("node:fs/promises");
			let entries;
			try {
				entries = await readdir(dir, { withFileTypes: true });
			} catch {
				return;
			}
			for (const entry of entries) {
				const fullPath = pathResolve(dir, entry.name);
				if (entry.isDirectory()) {
					if (!nonProseDirs.has(entry.name)) {
						await walk(fullPath);
					}
				} else if (entry.isFile()) {
					const ext = entry.name.match(/\.(md|txt|adoc)$/i)?.[0];
					if (ext) {
						files.push(fullPath);
					}
				}
			}
		}
		await walk(resolved);
		if (files.length === 0) {
			return {
				content: [{ type: "text", text: "No prose files found." }],
				details: { isError: false, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined },
			};
		}
		args.push(...files);
		filePaths.push(...files);
	} else {
		args.push(resolved);
		filePaths.push(resolved);
	}

	// 3. Execute Vale.
	const result = await pi.exec("vale", args, { cwd: ctx.cwd, signal: ctx.signal });

	// 4. Branch on the exit code and use stdout in every branch.
	if (result.killed) {
		return {
			content: [
				{
					type: "text",
					text: "Prosecco was cancelled.",
				},
			],
			details: { isError: false, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined },
		};
	}

	if (result.code >= 2) {
		// Vale failed: bad flag, missing config, unreadable path, or E201 on individual files.
		const stderr = result.stderr.trim();
		// Parse stderr for E201 errors (YAML unmarshal failures on individual files).
		// Format: <file>:<line>:E201:yaml: unmarshal errors
		const e201Files = new Set();
		if (stderr) {
			const lines = stderr.split("\n");
			for (const line of lines) {
				if (line.includes(":E201:")) {
					// Extract file path (everything before :<number>:E201:).
					const match = line.match(/^(.+?):\d+:E201:/);
					if (match) {
						e201Files.add(match[1]);
					}
				}
			}
		}

		if (e201Files.size > 0) {
			// Some files had YAML parsing errors. Retry without them.
			const retryArgs = args.slice(0, args.length - filePaths.length); // Remove the file paths.
			if (statResult.isDirectory()) {
				// Filter out the failed files from the file list.
				const goodFiles = filePaths.filter((f) => !e201Files.has(f));
				if (goodFiles.length === 0) {
					return {
						content: [
							{
								type: "text",
								text: `All ${e201Files.size} prose file(s) had YAML parsing errors and were skipped.`,
							},
						],
						details: {
								isError: false,
								violationCount: 0,
								filesScanned: 0,
								bySeverity: {},
								raw: undefined,
								e201Files: [...e201Files],
							},
						};
				}
				retryArgs.push(...goodFiles);
			} else {
				// Single file failed.
				if (e201Files.has(resolved)) {
					return {
						content: [
							{
								type: "text",
								text: `File could not be parsed: ${resolved}`,
							},
						],
						details: {
								isError: false,
								violationCount: 0,
								filesScanned: 0,
								bySeverity: {},
								raw: undefined,
								e201Files: [...e201Files],
							},
						};
				}
			}

			// Retry with the filtered file list.
			const retryResult = await pi.exec("vale", retryArgs, { cwd: ctx.cwd, signal: ctx.signal });
			if (retryResult.killed) {
				return {
					content: [{ type: "text", text: "Prosecco was cancelled." }],
					details: { isError: false, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined },
				};
			}
			if (retryResult.code >= 2) {
				const detail = retryResult.stderr.trim() || `vale exited ${retryResult.code} with arguments: ${retryArgs.join(" ")}`;
				return {
					content: [{ type: "text", text: `Prosecco failed: ${detail}` }],
					details: { isError: true, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined, args: retryArgs },
				};
			}
			// Process the retry result as normal.
			result = retryResult;
		} else {
			// Real Vale failure (bad flag, missing config, etc.).
			const detail = stderr || `vale exited ${result.code} with arguments: ${args.join(" ")}`;
			return {
				content: [{ type: "text", text: `Prosecco failed: ${detail}` }],
				details: { isError: true, violationCount: 0, filesScanned: 0, bySeverity: {}, raw: undefined, args },
			};
		}
	}

	const stdout = result.stdout;

	// 5b. Run spaCy checks if requested.
	let spacyOutput = "";
	if (spacy && filePaths.length > 0 && filePaths[0].endsWith('.md')) {
		const spacyModule = pathResolve("/home/pi/.pi/agent/extensions/prosecco/spacy", "spacy_asd-ste100.py");
		try {
			const spacyResult = await pi.exec("uv", ["run", "python", spacyModule, filePaths[0]], {
				cwd: ctx.cwd,
				signal: ctx.signal,
				env: { PYTHONIOENCODING: "utf-8" },
			});
			if (!spacyResult.killed && spacyResult.code === 0) {
				spacyOutput = spacyResult.stdout;
			}
		} catch {
			// spaCy script failed; continue without it
		}
	}

	if (outputFormat === "json") {
		// 5. Parse JSON and count alerts.
		let data;
		try {
			data = JSON.parse(stdout);
		} catch (parseErr) {
			return {
				content: [
					{
						type: "text",
						text: `Prosecco produced JSON but the output is not valid JSON: ${parseErr.message}\n\nRaw output:\n${stdout}`,
					},
				],
				details: {
					isError: false,
					violationCount: 0,
					filesScanned: 0,
					bySeverity: {},
					raw: stdout,
				},
			};
		}
		const { total, bySeverity } = countAlertsFromJson(data);
		const filesScanned = Object.keys(data || {}).length;
		const summary =
			total === 0
				? "No problems found."
				: `Found ${total} alert${total === 1 ? "" : "s"} (${Object.entries(bySeverity)
						.map(([k, v]) => `${v} ${k}(s)`)
						.join(", ")}) in ${filesScanned} file${filesScanned === 1 ? "" : "s"}.`;
		return {
			content: [{ type: "text", text: summary }],
			details: {
				isError: false,
				violationCount: total,
				filesScanned,
				bySeverity,
				raw: stdout,
			},
		};
	}

	// 6. Text (line) output.
	const lines = stdout.trim() === "" ? [] : stdout.trim().split("\n");
	const formatted = lines.map(formatLineAlert);

	// Parse spaCy output if present.
	let spacyAlerts = [];
	if (spacyOutput) {
		const spacyLines = spacyOutput.trim().split("\n").filter(l => l && !l.startsWith("No ") && !l.startsWith("Found ") && !l.startsWith("---"));
		spacyAlerts = spacyLines.map(l => l.trim()).filter(l => l && l.includes(":"));
	}

	// Combine Vale and spaCy results.
	const allAlerts = [...formatted, ...spacyAlerts];
	const filesScanned = new Set([...formatted.map((l) => l.split(":")[0]), ...spacyAlerts.map((l) => l.split(":")[0])]).size;

	const body =
		allAlerts.length === 0
			? "No problems found."
			: allAlerts.join("\n");

	const totalViolations = allAlerts.length;
	const spacyCount = spacyAlerts.length;
	const valeCount = formatted.length;

	return {
		content: [{ type: "text", text: body }],
		details: {
			isError: false,
			violationCount: totalViolations,
			filesScanned,
			bySeverity: {},
			raw: undefined,
			valeCount,
			spacyCount,
		},
	};
}

/**
 * Build a status summary for ctx.ui.setStatus().
 */
function buildStatus(total, filesScanned, bySeverity) {
	const parts = [`prosecco: ${total} alert${total === 1 ? "" : "s"}`];
	if (filesScanned > 0) {
		parts.push(`${filesScanned} file${filesScanned === 1 ? "" : "s"}`);
	}
	for (const [sev, count] of Object.entries(bySeverity)) {
		parts.push(`${count} ${sev}(s)`);
	}
	return parts.join(" | ");
}

export default function (pi) {
	// ── Tool: prosecco_lint ──────────────────────────────────────────────────
	pi.registerTool({
		name: "prosecco",
		label: "Prose Lint",
		description:
			"Run Vale + spaCy prose linting on a file or directory. Reports ASD-STE100 problems.",
		promptSnippet: "Run prose linting with Vale and spaCy",
		parameters: LintParams,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const userPath = params.path ?? undefined;
			const minAlertLevel = params.minAlertLevel ?? "suggestion";
			const outputFormat = params.outputFormat ?? "text";
			const steOnly = false;
			const spacy = true;
			return runLint(pi, userPath, minAlertLevel, outputFormat, steOnly, spacy, ctx);
		},
	});

	// ── Command: /prosecco ─────────────────────────────────────────────
	pi.registerCommand("prosecco", {
		description: "Lint prose with Vale + spaCy. Usage: /prosecco [path] [--minAlertLevel=suggestion|warning|error]",
		handler: async (args, ctx) => {
			// Parse arguments. The first non-flag token is the path.
			let userPath = undefined;
			let minAlertLevel = "suggestion";
			let steOnly = false;
			let spacy = true;
			const tokens = String(args || "").trim().split(/\s+/);
			for (const tok of tokens) {
				if (tok.startsWith("--minAlertLevel=")) {
					const val = tok.split("=", 2)[1];
					if (val === "suggestion" || val === "warning" || val === "error") {
						minAlertLevel = val;
					}
				} else if (!tok.startsWith("--")) {
					userPath = tok;
				}
			}

			if (!userPath) {
				ctx.ui.notify("Usage: /prosecco <path> [--minAlertLevel=suggestion|warning|error]");
				return;
			}

			const result = await runLint(pi, userPath, minAlertLevel, "text", steOnly, spacy, ctx);

			// Print the result regardless of UI mode — the user may be in
			// print-only mode and needs to see the output.
			const text = result.content[0]?.text ?? "";

			if (ctx.hasUI) {
				// Show a summary plus the first N alerts; tell the user to call
				// the tool for the rest when output is long.
				const lines = text.split("\n");
				const MAX_DISPLAY = 20;
				if (lines.length > MAX_DISPLAY) {
					const summary = lines.slice(0, MAX_DISPLAY).join("\n");
					ctx.ui.notify(
						`Prosecco helped you find ${result.details.violationCount} issues. Showing first ${MAX_DISPLAY}:\n\n${summary}\n\n... and ${lines.length - MAX_DISPLAY} more. Ask me to read the same files with prosecco for full output.`,
						result.details.isError ? "error" : "info",
					);
				} else {
					ctx.ui.notify(text, result.details.isError ? "error" : "info");
				}

				// Status line: always show a count.
				ctx.ui.setStatus(
					"prose-lint",
					buildStatus(
						result.details.violationCount,
						result.details.filesScanned,
						result.details.bySeverity,
					),
				);
			} else {
				// No UI — print the raw result.
				console.log(text);
			}
		},
	});
}
