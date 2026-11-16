// The launch environment binds the version and repository scope. Tool inputs only select symbols.
import { execFile } from "node:child_process";
import { Type } from "typebox";

const queryText = () => Type.String({ minLength: 1, maxLength: 256 });
const limit = Type.Optional(Type.Integer({ minimum: 1, maximum: 50 }));

function query(operation, params, signal, versionId) {
  return new Promise((resolve) => {
    const unavailable = {
      status: "unavailable", kind: "navigation", versionId, count: 0,
      message: "结构查询暂不可用，可以继续使用源码工具",
    };
    const child = execFile(
      process.env.CODEAGENT_PI_AST_PYTHON,
      ["-m", "business_code_agent.codeintel.ast.query_cli", operation],
      { cwd: process.env.CODEAGENT_PI_AST_IMPORT_ROOT, signal, timeout: 60000, maxBuffer: 512 * 1024 },
      (error, stdout) => {
        if (error) {
          resolve(unavailable);
          return;
        }
        try {
          resolve(JSON.parse(stdout));
        } catch {
          resolve(unavailable);
        }
      },
    );
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(params), "utf8");
  });
}

export default function (pi) {
  const { versionId } = JSON.parse(process.env.CODEAGENT_PI_AST_SCOPE);
  const tools = [
    {
      name: "find_code_symbol", operation: "find_symbol",
      description: "Find indexed classes, methods or web symbols. Returns workspace source paths and line ranges for read.",
      parameters: Type.Object({ query: queryText(), repositoryId: Type.Optional(queryText()), limit }),
    },
    {
      name: "get_code_symbol", operation: "get_symbol",
      description: "Locate a symbol by its symbolId from the same AST version.",
      parameters: Type.Object({ symbolId: queryText() }),
    },
    {
      name: "find_code_relations", operation: "find_relations",
      description: "Find indexed calls, inheritance or implementations. Results are navigation; raw calls do not identify a target.",
      parameters: Type.Object({
        symbolId: queryText(),
        relationType: Type.Optional(Type.Union([Type.Literal("CALLS"), Type.Literal("INHERITS"), Type.Literal("IMPLEMENTS")])),
        direction: Type.Optional(Type.Union([Type.Literal("outgoing"), Type.Literal("incoming")])),
        limit,
      }),
    },
  ];
  for (const { name, operation, description, parameters } of tools) {
    pi.registerTool({
      name, label: name, description, parameters,
      async execute(_callId, params, signal) {
        const result = await query(operation, params, signal, versionId);
        const { status, count } = result;
        return { content: [{ type: "text", text: JSON.stringify(result) }], details: { status, count, versionId } };
      },
    });
  }
}
