import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  SCHEMA_VERSION,
  buildBaseRecord,
  buildStatusPath,
  buildTempPath,
  createSessionAgentId,
  getStatusDir,
  nowUtc,
  sanitizeAgentId,
  summarizePrompt,
  updateRuntime,
  withTask,
  writeStatusFile,
} from "../index.js";

test("getStatusDir follows standard", () => {
  assert.equal(getStatusDir({ AGENT_STATUS_DIR: "/tmp/custom" }, "/home/me"), "/tmp/custom");
  assert.equal(getStatusDir({ XDG_STATE_HOME: "/state" }, "/home/me"), "/state/agent-status");
  assert.equal(getStatusDir({}, "/home/me"), "/home/me/.local/state/agent-status");
});

test("buildBaseRecord creates minimal valid record", () => {
  const record = buildBaseRecord({ agentId: "pi-123", workspace: "/work", pid: 12, now: "2026-06-20T16:45:00Z" });
  assert.deepEqual(record, {
    schema_version: SCHEMA_VERSION,
    agent_id: "pi-123",
    agent_name: "pi",
    runtime: {
      lifecycle: "running",
      updated_at: "2026-06-20T16:45:00Z",
      pid: 12,
      workspace: path.resolve("/work"),
    },
  });
});

test("task add and clear works", () => {
  const base = buildBaseRecord({ agentId: "pi-1", now: "2026-06-20T16:45:00Z" });
  const withWorkingTask = withTask(base, { state: "working", summary: "test", status_timestamp: "2026-06-20T16:45:01Z" });
  assert.equal(withWorkingTask.task.state, "working");
  const cleared = withTask(withWorkingTask, undefined);
  assert.equal("task" in cleared, false);
});

test("runtime update bumps fields", () => {
  const base = buildBaseRecord({ agentId: "pi-1", now: "2026-06-20T16:45:00Z" });
  const updated = updateRuntime(base, { lifecycle: "stopped", last_activity_at: "2026-06-20T16:45:02Z" }, "2026-06-20T16:45:03Z");
  assert.equal(updated.runtime.lifecycle, "stopped");
  assert.equal(updated.runtime.last_activity_at, "2026-06-20T16:45:02Z");
  assert.equal(updated.runtime.updated_at, "2026-06-20T16:45:03Z");
});

test("session agent ids are unique even in one pid context", () => {
  const ids = new Set(Array.from({ length: 8 }, () => createSessionAgentId()));
  assert.equal(ids.size, 8);
  for (const id of ids) assert.match(id, /^pi-[0-9a-f-]+$/);
});

test("distinct agent ids map to distinct status paths", () => {
  const first = createSessionAgentId();
  const second = createSessionAgentId();
  assert.notEqual(first, second);
  assert.notEqual(
    buildStatusPath(first, { AGENT_STATUS_DIR: "/tmp/x" }, "/home/me"),
    buildStatusPath(second, { AGENT_STATUS_DIR: "/tmp/x" }, "/home/me"),
  );
});

test("writeStatusFile writes parseable json atomically", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "agent-status-pi-"));
  const filePath = path.join(tmp, "pi-1.json");
  const record = buildBaseRecord({ agentId: "pi-1", now: nowUtc() });
  writeStatusFile(filePath, record);
  writeStatusFile(filePath, updateRuntime(record, { last_activity_at: nowUtc() }));
  const loaded = JSON.parse(fs.readFileSync(filePath, "utf8"));
  assert.equal(loaded.agent_id, "pi-1");
  assert.equal(typeof loaded.runtime.updated_at, "string");
});

test("temp path uses random suffix, not pid suffix", () => {
  const filePath = "/tmp/pi-1.json";
  const first = buildTempPath(filePath);
  const second = buildTempPath(filePath);
  assert.match(first, /^\/tmp\/\.pi-1\.json\.[0-9a-f-]+\.tmp$/);
  assert.notEqual(first, second);
  assert.equal(first.includes(`.${process.pid}.`), false);
});

test("helpers sanitize id and summarize prompt", () => {
  assert.equal(sanitizeAgentId("pi / 123"), "pi-123");
  assert.equal(sanitizeAgentId(""), "pi");
  assert.equal(buildStatusPath("pi / 123", { AGENT_STATUS_DIR: "/tmp/x" }, "/home/me"), "/tmp/x/pi-123.json");
  assert.equal(summarizePrompt("  hello\nworld  "), "hello world");
  assert.match(summarizePrompt("x".repeat(200)), /^x+…$/);
});
