// PreToolUse guard for the loop's agents. Exit 2 blocks the tool call and shows stderr to the agent.
// Who is running is LOOP_ROLE (attacker | defender | critic), set by scripts/loop_main.py; without it only the
// common rules apply. The Bash checks are best-effort (they read the command text): the file rules are the main
// guard here, and scripts/loop_main.py checks every run afterwards (changed files, the defender's tool calls).
const path = require("path");

const ROLE = (process.env.LOOP_ROLE || "").toLowerCase();
const CLONE = (process.env.PHASE1_DIR || "kosis-ai-nlp-summary-T1-30").toLowerCase().replace(/\/+$/, "") + "/";

// No agent edits these: the judge, the scripts, the settings, the role documents, the fixed entry of the number path.
const PROTECTED = ["evals/", "scripts/", ".claude/", "loop/config.env", "loop/results.tsv", "loop/prompt.md",
  "loop/prompts/", "loop/defender_allow.txt", "goal.md", "agents.md", "docs/attacker.md", "docs/defender.md",
  "docs/critic.md", CLONE + "kosis_chat/calc.py"];
// What each role may write with the file tools (path prefixes). Anything else is blocked for that role.
const MAY_WRITE = {
  attacker: ["loop/attack/spec_"],
  critic: ["loop/attack/", "loop/defender/brief_", "loop/critic/", "loop/log.md", "loop/learnings.md",
    "docs/current-state.md", "tasks.md", "loop/blocked.md"],
  defender: [CLONE, "loop/defender/notes_", "loop/blocked.md"],
};
// What the defender may not read or search: the items, the runs, the attack data.
const HIDDEN = ["evals/", "loop/attack/", "loop/runs/", "loop/config.env"];
const HIDDEN_TEXT = /(^|[\s"'=:(\\/])evals([\\/\s"')]|$)|golden_[\d*{\[]|items\.jsonl|loop[\\/]+(attack|runs)|config\.env|kosis_loop_vault/i;

const GIT_WRITE = /\bgit\s+(-C\s+\S+\s+)?(commit|reset|push|checkout|switch|clean|stash|rebase|merge|restore|revert|rm|mv|add)\b/;
const WRITE_VERBS = new Set(["rm", "mv", "cp", "tee", "truncate", "touch", "chmod"]);
const READ_TOOLS = new Set(["Read", "Grep", "Glob", "NotebookRead"]);

const root = process.env.CLAUDE_PROJECT_DIR || process.cwd();

function rel(p) {
  const r = path.relative(root, path.resolve(root, p));
  return r.split(path.sep).join("/").toLowerCase();
}
const under = (p, list) => {
  const r = rel(p);
  return list.some((x) => r === x.replace(/\/$/, "") || r.startsWith(x));
};
const isProtected = (p) => under(p, PROTECTED);
const isSecret = (p) => {
  const r = rel(p);
  return r === ".env" || r.endsWith("/.env") || r.startsWith("secrets/") || r.endsWith(".pem") || r.endsWith(".key");
};

function block(msg) {
  process.stderr.write(`Blocked by loop guard: ${msg}. See AGENTS.md (금지 사항).\n`);
  process.exit(2);
}

function checkBash(cmd) {
  if (GIT_WRITE.test(cmd)) block("git write commands are run by scripts/loop.sh only");
  if (ROLE === "defender" && HIDDEN_TEXT.test(cmd)) block("the defender does not read the evaluation or attack data");
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
  const p = input.file_path || input.notebook_path || input.path || "";
  if (p && isSecret(p)) block(`${p} is a secret file`);
  if (READ_TOOLS.has(data.tool_name)) {
    const where = [p, input.pattern || "", input.glob || ""].filter(Boolean);
    if (ROLE === "defender" && (where.some((w) => under(w, HIDDEN)) || where.some((w) => HIDDEN_TEXT.test(w)))) {
      block("the defender does not read the evaluation or attack data");
    }
    return;
  }
  if (p && isProtected(p)) block(`${p} is protected`);
  const scope = MAY_WRITE[ROLE];
  if (p && scope && !under(p, scope)) block(`the ${ROLE} does not write ${p}`);
});
