import { Type } from "typebox";
import { resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { execSync } from "node:child_process";

export default function (pi: any) {
  pi.registerTool({
    name: "edit",
    label: "Edit",
    description: "Edit a file by providing its absolute path and full content.",
    promptSnippet: "Provide the absolute path of the edited file and its full content as parameters. Tool call MUST include both mandatory parameters: path and content. Set path before you set content.",
    parameters: Type.Object({
      path: Type.String(),
      content: Type.String(),
    }),
    async execute(toolCallId: string, params: any, signal: AbortSignal, onUpdate: any, ctx: any) {
      const absolutePath = resolve(ctx.cwd, params.path);
      const uuid = randomUUID();
      const tempFullFilePath = `/tmp/${uuid}.full`;
      const tempPatchFilePath = `/tmp/${uuid}.patch`;

      try {
        // 1. Generate the full file into /tmp with uuid name
        await writeFile(tempFullFilePath, params.content, "utf8");

        // 2. Check if original file exists
        if (!existsSync(absolutePath)) {
          await writeFile(absolutePath, params.content, "utf8");
          return {
            content: [{ type: "text", text: `File ${params.path} did not exist. Created it with the provided content.` }],
          };
        }

        // 3. Taking a diff between the generated full file and the original file using unix command line diff tool
        let diffOutput = "";
        try {
          // diff -u produces a unified diff
          diffOutput = execSync(`diff -u "${absolutePath}" "${tempFullFilePath}"`, { encoding: "utf8" });
        } catch (e: any) {
          // diff returns exit code 1 if there are differences
          diffOutput = e.stdout || "";
        }

        if (!diffOutput.trim()) {
          return {
            content: [{ type: "text", text: `No changes detected for ${params.path}.` }],
          };
        }

        // 4. Evaluating if diff is fit for purpose
        if (!diffOutput.includes("---") || !diffOutput.includes("+++")) {
          throw new Error("The generated diff does not appear to be a valid unified diff.");
        }

        // 5. Applying the diff using unix command line patch tool
        await writeFile(tempPatchFilePath, diffOutput, "utf8");
        execSync(`patch "${absolutePath}" < "${tempPatchFilePath}"`);

        return {
          content: [{ type: "text", text: `Successfully applied changes to ${params.path} using diff/patch.` }],
          details: {
            diff: diffOutput,
            tempFullFile: tempFullFilePath,
            tempPatchFile: tempPatchFilePath,
          },
        };
      } catch (error: any) {
        throw new Error(`Failed to edit file: ${error.message}`);
      }
    },
  });
}
