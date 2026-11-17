/**
 * CodeAgent read-only search tools for Pi RPC.
 *
 * The project workspace exposes authorized repositories as directory symlinks.
 * Pi's built-in grep/find use ripgrep/fd without --follow, which silently hides
 * every file under those links. These replacements keep the same tool names but
 * search the authorized real roots directly and map results back to workspace
 * paths. Repository-internal symlinks are not followed, so a link inside a
 * repository cannot widen the authorization scope.
 */

import { spawn } from "node:child_process";
import { accessSync, appendFileSync, constants, realpathSync } from "node:fs";
import path from "node:path";
import { StringDecoder } from "node:string_decoder";
import { getAgentDir } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { mapResultLine, parseMounts, resolveSearchTargets } from "./search_mounts.js";

const FILE_TOOLS = new Set(["read", "grep", "find", "ls"]);
const BLOCKED_TOOLS = new Set(["bash", "powershell", "edit", "write"]);
const PATH_KEYS = ["path", "paths", "directory", "dir", "cwd", "file", "files"];

function parseRoots() {
  try {
    const values = JSON.parse(process.env.CODEAGENT_PI_ROOTS || "[]");
    return [...new Set(values.map((value) => path.resolve(String(value))))];
  } catch {
    return [];
  }
}

const ROOTS = parseRoots();
const WORKSPACE = path.resolve(process.env.CODEAGENT_PI_WORKSPACE || process.cwd());
const ALLOWED = ROOTS.length ? ROOTS : [WORKSPACE];
const MOUNTS = parseMounts(process.env.CODEAGENT_PI_MOUNTS || "{}", ALLOWED);

function isWithin(target) {
  return ALLOWED.some((root) => {
    const relative = path.relative(root, target);
    return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
  });
}

function checkPath(raw, cwd) {
  if (typeof raw !== "string" || !raw) return null;
  if (raw.startsWith("~")) return `禁止访问主目录路径：${raw}`;
  const resolved = path.resolve(cwd || WORKSPACE, raw);
  if (!isWithin(resolved)) return `路径不在本轮授权范围：${raw}`;
  try {
    const real = realpathSync(resolved);
    if (!isWithin(real)) return `符号链接指向本轮授权范围之外：${raw}`;
  } catch {
    // A non-existent or unreadable path is handled by the tool itself.
  }
  return null;
}

function checkToolInput(input, cwd) {
  if (!input || typeof input !== "object") return null;
  for (const key of PATH_KEYS) {
    const value = input[key];
    if (Array.isArray(value)) {
      for (const item of value) {
        const reason = checkPath(item, cwd);
        if (reason) return reason;
      }
    } else {
      const reason = checkPath(value, cwd);
      if (reason) return reason;
    }
  }
  for (const key of ["glob", "pattern"]) {
    const value = input[key];
    if (typeof value === "string" && value.replace(/\\/g, "/").split("/").includes("..")) {
      return `禁止使用越界匹配模式：${value}`;
    }
  }
  return null;
}

function executable(name) {
  const candidate = path.join(getAgentDir(), "bin", name);
  try {
    accessSync(candidate, constants.X_OK);
    return candidate;
  } catch {
    return name;
  }
}

function run(command, args, cwd, signal, maxBytes = 1024 * 1024) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { cwd, stdio: ["ignore", "pipe", "pipe"] });
    } catch (error) {
      resolve({ code: -1, output: "", error: String(error), aborted: false });
      return;
    }
    let output = "";
    let error = "";
    let aborted = false;
    let truncated = false;
    let finished = false;
    const outDecoder = new StringDecoder("utf8");
    const errDecoder = new StringDecoder("utf8");
    const onAbort = () => {
      aborted = true;
      child.kill("SIGTERM");
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    child.stdout.on("data", (chunk) => {
      output += outDecoder.write(chunk);
      if (output.length > maxBytes) {
        truncated = true;
        output = output.slice(0, maxBytes);
        child.kill("SIGTERM");
      }
    });
    child.stderr.on("data", (chunk) => {
      if (error.length < 4000) error += errDecoder.write(chunk);
    });
    const finish = (code) => {
      if (finished) return;
      finished = true;
      output += outDecoder.end();
      error += errDecoder.end();
      signal?.removeEventListener("abort", onAbort);
      resolve({ code, output, error, aborted, truncated });
    };
    child.on("error", (exc) => {
      error += (error ? "\n" : "") + exc.message;
      finish(-1);
    });
    child.on("close", (code) => finish(code ?? -1));
  });
}

function limited(lines, limit, notice) {
  if (lines.length <= limit) return lines.join("\n");
  return [...lines.slice(0, limit), "", `[${notice}；结果已截断]`].join("\n");
}

const grepSchema = Type.Object({
  pattern: Type.String({ description: "Search pattern (regex or literal string)" }),
  path: Type.Optional(Type.String({ description: "Directory or file to search (default: current directory)" })),
  glob: Type.Optional(Type.String({ description: "Filter files by glob pattern, e.g. '*.ts' or '**/*.spec.ts'" })),
  ignoreCase: Type.Optional(Type.Boolean({ description: "Case-insensitive search (default: false)" })),
  literal: Type.Optional(Type.Boolean({ description: "Treat pattern as literal string instead of regex (default: false)" })),
  context: Type.Optional(Type.Number({ description: "Number of lines to show before and after each match (default: 0)" })),
  limit: Type.Optional(Type.Number({ description: "Maximum number of matches to return (default: 100)" })),
});

const findSchema = Type.Object({
  pattern: Type.String({ description: "Glob pattern to match files, e.g. '*.ts', '**/*.json', or 'src/**/*.spec.ts'" }),
  path: Type.Optional(Type.String({ description: "Directory to search in (default: current directory)" })),
  limit: Type.Optional(Type.Number({ description: "Maximum number of results (default: 1000)" })),
});

export default function (pi) {
  const capturePath = process.env.CODEAGENT_PI_PROMPT_TRACE;
  if (capturePath) {
    let previous;
    pi.on("before_provider_request", (_event, ctx) => {
      const active = new Set(pi.getActiveTools());
      const tools = pi.getAllTools().filter(({ name }) => active.has(name))
        .map(({ name, description }) => ({ name, description }));
      const snapshot = JSON.stringify({ text: ctx.getSystemPrompt(), tools });
      if (snapshot !== previous) {
        try {
          appendFileSync(capturePath, snapshot + "\n", "utf8");
          previous = snapshot;
        } catch {
          // A failed trace write leaves the model request untouched.
        }
      }
    });
  }
  pi.on("tool_call", async (event) => {
    if (BLOCKED_TOOLS.has(event.toolName)) {
      return { block: true, reason: "CodeAgent 只读模式禁用该工具" };
    }
    if (!FILE_TOOLS.has(event.toolName)) return;
    const reason = checkToolInput(event.input, process.cwd());
    if (reason) return { block: true, reason };
  });

  pi.registerTool({
    name: "grep",
    label: "grep",
    description: "Search file contents for a pattern. Searches authorized roots and returns workspace-relative paths with line numbers.",
    parameters: grepSchema,
    async execute(_toolCallId, params, signal) {
      const cwd = process.cwd();
      const searchPath = params.path || ".";
      const reason = checkPath(searchPath, cwd);
      if (reason) {
        return { content: [{ type: "text", text: `Access denied: ${reason}` }], details: { denied: true } };
      }
      const plan = resolveSearchTargets(WORKSPACE, MOUNTS, searchPath);
      if (!plan.targets.length) {
        return { content: [{ type: "text", text: `Access denied: ${plan.reason}` }],
          details: { denied: true } };
      }
      const limit = Math.max(1, params.limit || 100);
      const lines = [];
      let failure = "";
      let truncated = false;
      for (const target of plan.targets) {
        if (signal?.aborted) {
          return { content: [{ type: "text", text: "Search aborted" }], details: { aborted: true } };
        }
        const args = ["--no-config", "--line-number", "--no-heading", "--with-filename",
          "--color=never", "--hidden"];
        if (params.ignoreCase) args.push("--ignore-case");
        if (params.literal) args.push("--fixed-strings");
        if (params.glob) args.push("--glob", params.glob);
        if (params.context && params.context > 0) args.push("--context", String(params.context));
        args.push("--", params.pattern, target.target);
        const result = await run(executable("rg"), args, target.cwd, signal);
        if (result.aborted) {
          return { content: [{ type: "text", text: "Search aborted" }], details: { aborted: true } };
        }
        if (result.code === 1 && !result.output.trim()) continue;
        if (result.code !== 0) {
          failure = failure || (result.error.trim() || `exit ${result.code}`);
          continue;
        }
        truncated = truncated || result.truncated;
        for (const line of result.output.trimEnd().split("\n").filter(Boolean)) {
          lines.push(mapResultLine(line, target.prefix));
        }
        if (lines.length >= limit * 20) {
          truncated = true;
          break;
        }
      }
      if (!lines.length) {
        if (failure) {
          return { content: [{ type: "text", text: `grep failed: ${failure}` }],
            details: { error: true } };
        }
        return { content: [{ type: "text", text: "No matches found" }], details: { matches: 0 } };
      }
      const text = limited(lines, limit, "匹配数量达到上限");
      return { content: [{ type: "text", text }],
        details: { matches: lines.length, truncated: truncated || lines.length > limit } };
    },
  });

  pi.registerTool({
    name: "find",
    label: "find",
    description: "Find files by glob pattern. Searches authorized roots and returns workspace-relative paths.",
    parameters: findSchema,
    async execute(_toolCallId, params, signal) {
      const cwd = process.cwd();
      const searchPath = params.path || ".";
      const reason = checkPath(searchPath, cwd);
      if (reason) {
        return { content: [{ type: "text", text: `Access denied: ${reason}` }], details: { denied: true } };
      }
      const plan = resolveSearchTargets(WORKSPACE, MOUNTS, searchPath);
      if (!plan.targets.length) {
        return { content: [{ type: "text", text: `Access denied: ${plan.reason}` }],
          details: { denied: true } };
      }
      const limit = Math.max(1, params.limit || 1000);
      const lines = [];
      let failure = "";
      let truncated = false;
      for (const target of plan.targets) {
        if (signal?.aborted) {
          return { content: [{ type: "text", text: "Search aborted" }], details: { aborted: true } };
        }
        const args = ["--glob", "--color=never", "--hidden",
          "--max-results", String(limit)];
        if (params.pattern.includes("/")) args.push("--full-path");
        args.push("--", params.pattern, target.target);
        let result = await run(executable("fd"), args, target.cwd, signal);
        if (result.code === -1 && /ENOENT|not found/i.test(result.error)) {
          result = await run(executable("rg"),
            ["--files", "--color=never", "--hidden", "--glob", params.pattern, target.target],
            target.cwd, signal);
        }
        if (result.aborted) {
          return { content: [{ type: "text", text: "Search aborted" }], details: { aborted: true } };
        }
        if ((result.code === 0 || result.code === 1) && !result.output.trim()) continue;
        if (result.code !== 0) {
          failure = failure || (result.error.trim() || `exit ${result.code}`);
          continue;
        }
        truncated = truncated || result.truncated;
        for (const line of result.output.trimEnd().split("\n").filter(Boolean)) {
          lines.push(mapResultLine(line, target.prefix));
        }
        if (lines.length >= limit * 2) {
          truncated = true;
          break;
        }
      }
      if (!lines.length) {
        if (failure) {
          return { content: [{ type: "text", text: `find failed: ${failure}` }],
            details: { error: true } };
        }
        return { content: [{ type: "text", text: "No files found matching pattern" }],
          details: { files: 0 } };
      }
      const text = limited(lines, limit, "文件数量达到上限");
      return { content: [{ type: "text", text }],
        details: { files: lines.length, truncated: truncated || lines.length >= limit } };
    },
  });

  // Registered last: the health check in PiAgentRuntime only passes when the
  // tool overrides above loaded successfully.
  pi.registerCommand("codeagent-readonly-search", {
    description: "CodeAgent 只读检索扩展已加载",
    handler: async () => {},
  });
}
