# A2A compatibility

`agent-status/v1alpha1` is an A2A-compatible local status layer. It complements A2A rather than replacing it. It does not replace A2A Agent Card discovery, Task APIs, or full service behavior.

A2A is the right layer for agent discovery and task interaction across processes or hosts. It is not meant to answer simple machine-local questions such as whether a process is alive, which workspace it is using, which PID owns it, or whether the latest heartbeat has gone stale.

## Mapping to the A2A Agent Card

Local fields:
- `a2a.agent_card_url`
- `a2a.service_url`

Meaning:
- `a2a.agent_card_url` points to the Agent Card discovery URL when the agent exposes one.
- `a2a.service_url` points to the A2A service endpoint when one is available.

These fields are optional because many local-only agents do not expose network endpoints.

## Mapping to A2A Task / TaskStatus / TaskState

Direct mappings:
- `task.id` -> A2A Task `id`
- `task.context_id` -> A2A Task `contextId`
- `task.state` -> A2A TaskStatus `state`
- `task.status_timestamp` -> A2A TaskStatus `timestamp`

Recommended state vocabulary:
- `submitted`
- `working`
- `input-required`
- `auth-required`
- `completed`
- `canceled`
- `rejected`
- `failed`
- `unknown`

## What remains local-only

These fields do not have direct A2A task equivalents and remain local runtime metadata:
- `runtime.lifecycle`
- `runtime.updated_at`
- `runtime.last_activity_at`
- `runtime.pid`
- `runtime.workspace`
- reader-derived states such as `idle`, `stale`, and `missing`

Why:
- A2A models work and service discovery.
- The local status file adds process and workstation visibility.
- Keeping that runtime view local avoids overloading A2A with host-specific metadata that many agents will never expose remotely.

## Compatibility posture

Preferred wording:
- "A2A-compatible local status layer"
- "local snapshot complements A2A"

Avoid wording such as:
- "A2A replacement"
- "full local A2A registry"
- "new task protocol"

## Practical use

A typical pairing looks like this:
1. A reader discovers a running local agent from the status directory.
2. The reader shows runtime state immediately.
3. If an `a2a` block exists, the reader can link out to the Agent Card or A2A service.
4. If no `a2a` block exists, the local status is still valid and useful.

## Bottom line

`agent-status/v1alpha1` provides lightweight local observability. A2A still owns remote discovery and task interaction. The two standards work well together because they answer different questions.
