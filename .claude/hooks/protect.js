// PreToolUse guard: stops the loop agent from editing its own judge or running git.
// Exit 2 blocks the tool call and shows stderr to the agent. The Bash check is
// best-effort (it inspects the command text), so the file rules are the main guard.
const path = require("path");

const PROTECTED = ["evals/", "scripts/", ".claude/", "loop/config.env",
  "loop/results.tsv", "loop/prompt.md", "goal.md"];
const GIT_WRITE = /\bgit\s+(commit|reset|push|checkout|switch|clean|stash|rebase|merge|restore|revert|rm|mv)\b/;
const WRITE_VERBS = new Set(["rm", "mv", "cp", "tee", "truncate", "touch", "chmod"]);

const root = process.env.CLAUDE_PROJECT_DIR || process.cwd();

function rel(p) {
  const r = path.relative(root, path.resolve(root, p));
  return r.split(path.sep).join("/").toLowerCase();
}
const isProtected = (p) => {
  const r = rel(p);
  return PROTECTED.some((x) => r === x.replace(/\/$/, "") || r.startsWith(x));
};
const isSecret = (p) => {
  const r = rel(p);
  return r === ".env" || r.startsWith("secrets/") || r.endsWith(".pem") || r.endsWith(".key");
};

function block(msg) {
  process.stderr.write(`Blocked by loop guard: ${msg}. See AGENTS.md (금지 사항).\n`);
  process.exit(2);
}

function checkBash(cmd) {
  if (GIT_WRITE.test(cmd)) block("git write commands are run by scripts/loop.sh only");
  for (const m of cmd.matchAll(/(?<![0-9&])>{1,2}\s*([^\s;|&]+)/g)) {
    if (isProtected(m[1])) block(`redirect into protected path ${m[1]}`);
  }
  const tokens = cmd.split(/[\s;|&]+/).map((t) => t.replace(/^['"]|['"]$/g, "")).filter(Boolean);
  const writes = tokens.some((t) => WRITE_VERBS.has(t)) || (tokens.includes("sed") && tokens.includes("-i"));
  if (writes && tokens.some((t) => !t.startsWith("-") && isProtected(t))) {
    block("shell command modifies a protected path");
  }
}

let raw = "";
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  const data = JSON.parse(raw || "{}");
  const input = data.tool_input || {};
  if (data.tool_name === "Bash") return checkBash(input.command || "");
  const p = input.file_path || input.notebook_path || "";
  if (p && isProtected(p)) block(`${p} is protected`);
  if (p && isSecret(p)) block(`${p} is a secret file`);
});
