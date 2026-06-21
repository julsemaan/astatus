import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const SCHEMA_VERSION = "agent-status/v1alpha1";
export const HEARTBEAT_INTERVAL_MS = 20_000;
const EXTENSION_LOADED = Symbol.for("agent-status.pi-extension.loaded");

export function nowUtc() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function getStatusDir(env = process.env, homeDir = os.homedir()) {
  if (env.AGENT_STATUS_DIR) return path.resolve(expandHome(env.AGENT_STATUS_DIR, homeDir));
  if (env.XDG_STATE_HOME) return path.resolve(expandHome(env.XDG_STATE_HOME, homeDir), "agent-status");
  return path.resolve(homeDir, ".local", "state", "agent-status");
}

export function sanitizeAgentId(value) {
  const text = String(value || "").trim();
  return text.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "pi";
}

export function createSessionAgentId(prefix = "pi") {
  return sanitizeAgentId(`${prefix}-${crypto.randomUUID()}`);
}

export function summarizePrompt(prompt, maxLength = 120) {
  const text = String(prompt || "").replace(/\s+/g, " ").trim();
  if (!text) return undefined;
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}…`;
}

export function buildStatusPath(agentId, env = process.env, homeDir = os.homedir()) {
  return path.join(getStatusDir(env, homeDir), `${sanitizeAgentId(agentId)}.json`);
}

export function buildBaseRecord({ agentId, workspace, pid = process.pid, lifecycle = "running", now = nowUtc() }) {
  const runtime = {
    lifecycle,
    updated_at: now,
    pid,
  };

  if (workspace) runtime.workspace = path.resolve(workspace);

  return {
    schema_version: SCHEMA_VERSION,
    agent_id: sanitizeAgentId(agentId),
    agent_name: "pi",
    runtime,
  };
}

export function withTask(record, task) {
  const next = structuredClone(record);
  if (!task) {
    delete next.task;
    return next;
  }
  next.task = task;
  return next;
}

export function updateRuntime(record, patch = {}, now = nowUtc()) {
  const next = structuredClone(record);
  next.runtime = {
    ...next.runtime,
    ...patch,
    updated_at: patch.updated_at || now,
  };
  return next;
}

export function buildTempPath(filePath) {
  return path.join(path.dirname(filePath), `.${path.basename(filePath)}.${crypto.randomUUID()}.tmp`);
}

export function writeStatusFile(filePath, record) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const data = `${JSON.stringify(record, null, 2)}\n`;
  const tempPath = buildTempPath(filePath);
  const fd = fs.openSync(tempPath, "wx", 0o600);
  try {
    fs.writeFileSync(fd, data, "utf8");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(tempPath, filePath);
}

function expandHome(value, homeDir) {
  if (!value.startsWith("~")) return value;
  if (value === "~") return homeDir;
  if (value.startsWith("~/")) return path.join(homeDir, value.slice(2));
  return value;
}

export default function agentStatusPiExtension(pi) {
  if (pi[EXTENSION_LOADED]) return;
  pi[EXTENSION_LOADED] = true;

  const agentId = createSessionAgentId();
  const statusPath = buildStatusPath(agentId);
  let heartbeat = undefined;
  let current = buildBaseRecord({ agentId });

  const flush = () => writeStatusFile(statusPath, current);
  const touch = (patch = {}) => {
    current = updateRuntime(current, patch);
    flush();
  };
  const setTask = (task) => {
    current = withTask(updateRuntime(current, { last_activity_at: nowUtc() }), task);
    flush();
  };
  const startHeartbeat = () => {
    if (heartbeat) return;
    heartbeat = setInterval(() => {
      current = updateRuntime(current);
      flush();
    }, HEARTBEAT_INTERVAL_MS);
    heartbeat.unref?.();
  };
  const stopHeartbeat = () => {
    if (!heartbeat) return;
    clearInterval(heartbeat);
    heartbeat = undefined;
  };

  pi.on("session_start", async (_event, ctx) => {
    current = buildBaseRecord({
      agentId,
      workspace: ctx.cwd,
      lifecycle: "running",
    });
    flush();
    startHeartbeat();
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const summary = summarizePrompt(event.prompt);
    setTask({
      state: "working",
      summary,
      status_timestamp: nowUtc(),
      // ponytail: use session file as cheap context id until pi exposes stable task ids here.
      ...(ctx.sessionManager?.getSessionFile?.() ? { context_id: String(ctx.sessionManager.getSessionFile()) } : {}),
    });
  });

  pi.on("tool_execution_start", async () => {
    touch({ last_activity_at: nowUtc() });
  });

  pi.on("tool_execution_end", async () => {
    touch({ last_activity_at: nowUtc() });
  });

  pi.on("agent_end", async () => {
    current = withTask(updateRuntime(current), undefined);
    flush();
  });

  pi.on("session_shutdown", async () => {
    stopHeartbeat();
    current = withTask(updateRuntime(current, { lifecycle: "stopped" }), undefined);
    flush();
  });
}
