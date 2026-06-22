import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import agentStatusPiExtension from "../index.js";

/**
 * Build a mock pi with both lifecycle `on` and extension `events`.
 * handlers: Map<eventName, handler> for lifecycle events
 * listeners: Map<eventName, handler[]> for extension-to-extension events
 */
function createMockPi() {
  const lifecycleHandlers = new Map();
  const eventListeners = new Map();

  const pi = {
    on(event, handler) {
      lifecycleHandlers.set(event, handler);
    },
    events: {
      on(event, handler) {
        const list = eventListeners.get(event) || [];
        list.push(handler);
        eventListeners.set(event, list);
      },
      emit(event, data) {
        const list = eventListeners.get(event) || [];
        // snapshot to avoid mutation during iteration
        for (const handler of [...list]) {
          handler(data);
        }
      },
    },
  };

  return { pi, lifecycleHandlers, eventListeners };
}

function readStatusFile(dir) {
  const files = fs.readdirSync(dir);
  assert.equal(files.length, 1);
  const rec = JSON.parse(fs.readFileSync(path.join(dir, files[0]), "utf8"));
  return rec;
}

/**
 * Minimal sessionManager for reconstructing todo/goal/mode state.
 * Branch entries are reconstructed from message/custom entry arrays.
 */
function sessionManager(entries = []) {
  return {
    getBranch() {
      return entries;
    },
  };
}

function testCtx(name) {
  return {
    cwd: `/tmp/${name}`,
    sessionManager: sessionManager([]),
  };
}

test("composite: bridge event overrides prompt task", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);

    // Start session
    const ctx = testCtx("bridge-override");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Before agent: sets core prompt task
    await lifecycleHandlers.get("before_agent_start")?.(
      { prompt: "Do the thing" },
      ctx,
    );

    const recordAfterPrompt = readStatusFile(tmp);
    assert.equal(recordAfterPrompt.task.state, "working");
    assert.ok(recordAfterPrompt.task.summary?.includes("Do the thing"));

    // Bridge emits override
    pi.events.emit("agent-status:profile", {
      task: { state: "input-required", summary: "Waiting for user input" },
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 0, done: 0 },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    const recordAfterBridge = readStatusFile(tmp);
    assert.equal(recordAfterBridge.task.state, "input-required");
    assert.equal(recordAfterBridge.task.summary, "Waiting for user input");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: question blocked → input-required survives agent_end", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("question-blocked");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Bridge says input-required (question blocked)
    pi.events.emit("agent-status:profile", {
      task: { state: "input-required", summary: "Waiting for user input" },
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 0, done: 0 },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    // Agent ends — core task cleared, but bridge task survives
    await lifecycleHandlers.get("agent_end")?.({}, ctx);

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "input-required");
    assert.equal(record.x_meta.pi.mode, "build");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: open todo while idle → submitted", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("open-todo-idle");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Bridge emits submitted (2 open todos, idle)
    pi.events.emit("agent-status:profile", {
      task: { state: "submitted", summary: "2 open todos pending" },
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 2, done: 1, current: "Fix bug" },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "submitted");
    assert.equal(record.x_meta.pi.todo.open, 2);
    assert.equal(record.x_meta.pi.todo.current, "Fix bug");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: active goal → working overrides prompt summary", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("active-goal");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Before agent: sets core prompt task
    await lifecycleHandlers.get("before_agent_start")?.(
      { prompt: "Generic work" },
      ctx,
    );

    // Bridge emits active goal (bridge priority > prompt)
    pi.events.emit("agent-status:profile", {
      task: { state: "working", summary: "Refactor auth module" },
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 0, done: 3 },
          goal: { active: true, status: "working", text: "Refactor auth module" },
          subagent: { active: false },
        },
      },
    });

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "working");
    // bridge task shows goal text, not prompt summary
    assert.equal(record.task.summary, "Refactor auth module");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: bridge without task falls back to core task", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("no-bridge-task");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Before agent: core prompt task
    await lifecycleHandlers.get("before_agent_start")?.(
      { prompt: "Core work" },
      ctx,
    );

    // Bridge emits NO task, only meta
    pi.events.emit("agent-status:profile", {
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 0, done: 0 },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    const record = readStatusFile(tmp);
    // Falls back to core prompt task
    assert.equal(record.task.state, "working");
    assert.ok(record.task.summary?.includes("Core work"));
    // x_meta still applied
    assert.equal(record.x_meta.pi.mode, "build");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: agent_end clears core task but preserves bridge snapshot", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("agent-end-preserve");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Before agent: sets core task
    await lifecycleHandlers.get("before_agent_start")?.(
      { prompt: "Working on X" },
      ctx,
    );

    // Bridge emits submitted (idle + open todos)
    pi.events.emit("agent-status:profile", {
      task: { state: "submitted", summary: "3 open todos pending" },
      x_meta: {
        pi: {
          mode: "plan",
          ponytail: "lite",
          todo: { open: 3, done: 1 },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    // Agent ends
    await lifecycleHandlers.get("agent_end")?.({}, ctx);

    const record = readStatusFile(tmp);
    // Core task cleared, but bridge "submitted" survives
    assert.equal(record.task.state, "submitted");
    // meta survives
    assert.equal(record.x_meta.pi.mode, "plan");
    assert.equal(record.x_meta.pi.ponytail, "lite");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: prompt task survives agent_end as submitted", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("sticky-after-agent-end");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);
    await lifecycleHandlers.get("before_agent_start")?.({ prompt: "Keep this task alive" }, ctx);
    await lifecycleHandlers.get("agent_end")?.({}, ctx);

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "submitted");
    assert.ok(record.task.summary?.includes("Keep this task alive"));
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: retained task survives idle heartbeat without bridge", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("sticky-heartbeat");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);
    await lifecycleHandlers.get("before_agent_start")?.({ prompt: "Idle but same session" }, ctx);
    await lifecycleHandlers.get("agent_end")?.({}, ctx);
    await lifecycleHandlers.get("tool_execution_start")?.({}, ctx);
    await lifecycleHandlers.get("tool_execution_end")?.({}, ctx);

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "submitted");
    assert.ok(record.task.summary?.includes("Idle but same session"));
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: second session_start clears retained task", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const firstCtx = {
      cwd: "/tmp/first-session",
      sessionManager: { getSessionFile: () => "/tmp/first-session.jsonl" },
    };
    const secondCtx = {
      cwd: "/tmp/second-session",
      sessionManager: { getSessionFile: () => "/tmp/second-session.jsonl" },
    };

    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, firstCtx);
    await lifecycleHandlers.get("before_agent_start")?.({ prompt: "Old session task" }, firstCtx);
    await lifecycleHandlers.get("agent_end")?.({}, firstCtx);

    let record = readStatusFile(tmp);
    assert.equal(record.task.state, "submitted");

    await lifecycleHandlers.get("session_start")?.({ reason: "switch" }, secondCtx);

    record = readStatusFile(tmp);
    assert.equal("task" in record, false);
    assert.equal(record.runtime.workspace, path.resolve("/tmp/second-session"));
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: bridge task still overrides retained task after idle", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("bridge-over-sticky");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);
    await lifecycleHandlers.get("before_agent_start")?.({ prompt: "Retained prompt task" }, ctx);
    await lifecycleHandlers.get("agent_end")?.({}, ctx);

    pi.events.emit("agent-status:profile", {
      task: { state: "input-required", summary: "Bridge still wins" },
      x_meta: {
        pi: {
          mode: "build",
          ponytail: "full",
          todo: { open: 1, done: 0 },
          goal: { active: false, status: "idle", text: "" },
          subagent: { active: false },
        },
      },
    });

    const record = readStatusFile(tmp);
    assert.equal(record.task.state, "input-required");
    assert.equal(record.task.summary, "Bridge still wins");
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: bridge meta includes full pi snapshot", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("full-meta");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Bridge with full meta
    pi.events.emit("agent-status:profile", {
      x_meta: {
        pi: {
          mode: "brainstorm",
          access: "read-only",
          profile: "pub",
          ponytail: "off",
          todo: { open: 5, done: 12, current: "Write docs" },
          goal: { active: true, status: "working", text: "Finish docs" },
          subagent: { active: true, mode: "chain", agents: ["reviewer", "worker"] },
        },
      },
    });

    const record = readStatusFile(tmp);
    const meta = record.x_meta.pi;
    assert.equal(meta.mode, "brainstorm");
    assert.equal(meta.access, "read-only");
    assert.equal(meta.profile, "pub");
    assert.equal(meta.ponytail, "off");
    assert.equal(meta.todo.open, 5);
    assert.equal(meta.todo.done, 12);
    assert.equal(meta.goal.active, true);
    assert.equal(meta.goal.text, "Finish docs");
    assert.equal(meta.subagent.active, true);
    assert.equal(meta.subagent.mode, "chain");
    assert.deepEqual(meta.subagent.agents, ["reviewer", "worker"]);
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: shutdown removes file even with bridge data", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("shutdown");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // Bridge emits some data
    pi.events.emit("agent-status:profile", {
      task: { state: "submitted", summary: "pending" },
      x_meta: {
        pi: { mode: "build", ponytail: "full", todo: { open: 1, done: 0 }, goal: { active: false, status: "idle", text: "" }, subagent: { active: false } },
      },
    });

    // Shutdown removes file
    await lifecycleHandlers.get("session_shutdown")?.({ reason: "quit" }, {});

    assert.deepEqual(fs.readdirSync(tmp), []);
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: no bridge data does not set x_meta", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("no-xmeta");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    const record = readStatusFile(tmp);
    // No x_meta without bridge event
    assert.equal("x_meta" in record, false);
    // No task yet
    assert.equal("task" in record, false);
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("composite: bridge x_meta cleared when bridge emits without x_meta", async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "astatus-comp-"));
  const prev = process.env.AGENT_STATUS_DIR;
  process.env.AGENT_STATUS_DIR = tmp;

  const { pi, lifecycleHandlers } = createMockPi();
  try {
    agentStatusPiExtension(pi);
    const ctx = testCtx("clear-xmeta");
    await lifecycleHandlers.get("session_start")?.({ reason: "startup" }, ctx);

    // First: bridge with x_meta
    pi.events.emit("agent-status:profile", {
      x_meta: {
        pi: { mode: "build", ponytail: "full", todo: { open: 0, done: 0 }, goal: { active: false, status: "idle", text: "" }, subagent: { active: false } },
      },
    });

    let record = readStatusFile(tmp);
    assert.ok("x_meta" in record);

    // Second: bridge without x_meta — should not happen in practice,
    // but the writer should handle it gracefully
    pi.events.emit("agent-status:profile", {});

    record = readStatusFile(tmp);
    assert.equal("x_meta" in record, false);
  } finally {
    if (prev === undefined) delete process.env.AGENT_STATUS_DIR;
    else process.env.AGENT_STATUS_DIR = prev;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
