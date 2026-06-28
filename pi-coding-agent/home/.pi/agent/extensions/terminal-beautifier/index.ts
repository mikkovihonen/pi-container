import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

interface TextContent {
  type: "text";
  text: string;
}

interface ThinkingContent {
  type: "thinking";
  thinking: string;
}

interface ToolCall {
  type: "toolCall";
  id: string;
  name: string;
  arguments: Record<string, any>;
}

type MessageBlock = TextContent | ThinkingContent | ToolCall;

interface MessageEndEvent {
  message: {
    role: string;
    content: MessageBlock[];
  };
}

interface MessageUpdateEvent {
  message: {
    role: string;
    content: MessageBlock[];
  };
  assistantMessageEvent?: {
    type: "assistant";
  };
}

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const loadJson = (filename: string) => {
  const filePath = path.join(__dirname, filename);
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
};

const LATEX_SYMBOLS: Record<string, string> = loadJson("latex-symbols.json");

export default function (pi: ExtensionAPI) {
  // Compile regex patterns once for performance
  const symbolPattern = new RegExp(
    Object.keys(LATEX_SYMBOLS)
      .sort((a, b) => b.length - a.length)
      .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|"),
    "g"
  );

  // Structural rules (converts 2D LaTeX to 1D terminal text)
  const structuralRules = [
    { pattern: /\\lim(?:_)?\{([^}]*)\}/g, replacement: "lim($1)" },
    { pattern: /\\frac\{([^}]*)\}\{([^}]*)\}/g, replacement: "($1/$2)" },
    { pattern: /\\sqrt\{([^}]*)\}/g, replacement: "√$1" },
    { pattern: /\\left\(([^)]*)\\right\)/g, replacement: "($1)" },
    { pattern: /\\left\[([^\]]*)\\right\]/g, replacement: "[$1]" },
    { pattern: /\\left\{([^}]*)\\right\}/g, replacement: "{$1}" },
    { pattern: /\\text\{([^}]*)\}/g, replacement: "$1" },
    { pattern: /\\quad/g, replacement: "  " },
    { pattern: /\\qquad/g, replacement: "    " },
  ];

const transform = (text: string, includeHeaders: boolean) => {
  // Split text into segments: [plain_text, code_block, plain_text, ...]
  const parts = text.split(/(```[\s\S]*?```)/g);

  const transformedParts = parts.map((part) => {
    // If this part is a code block, return it exactly as it is
    if (part.startsWith("```")) {
      return part;
    }

    let result = part;

    // Phase 1: Structural Flattening
    for (const rule of structuralRules) {
      result = result.replace(rule.pattern, rule.replacement);
    }

    // Phase 2: Symbol Replacement
    result = result.replace(symbolPattern, (matched) => LATEX_SYMBOLS[matched] || matched);

    // Phase 3: Header Color
    if (includeHeaders) {
      result = result.replace(/^(#+)\s+(.*)$/gm, "\x1b[1;97m$2\x1b[0m");
    }

    // Phase 4: LaTeX Delimiter Replacement
    const symbolValues = Object.values(LATEX_SYMBOLS);
    result = result.replace(/\$([^$]+)\$/g, (match, content) => {
      const hasSymbol = symbolValues.some((value) => content.includes(value));
      if (hasSymbol) {
        return `\x1b[36m${content}\x1b[0m`;
      }
      return match;
    });

    return result;
  });

  return transformedParts.join("");
};

  pi.on("message_end", async (event: MessageEndEvent, ctx: ExtensionContext) => {
    // Only beautify assistant messages
    if (event.message.role !== "assistant") return;

    const newContent = event.message.content.map((block: MessageBlock) => {
      if (block.type === "text") {
        return { ...block, text: transform(block.text, true) };
      }
      return block;
    });

    return {
      message: {
        ...event.message,
        content: newContent,
      },
    };
  });
}
