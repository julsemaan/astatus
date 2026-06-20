from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "agent-status/v1alpha1"
RUNTIME_LIFECYCLES = {"running", "stopped", "unknown"}
TASK_STATES = {
    "submitted",
    "working",
    "input-required",
    "auth-required",
    "completed",
    "canceled",
    "rejected",
    "failed",
    "unknown",
}
DEFAULT_STALE_AFTER = 60
DEFAULT_WATCH_INTERVAL = 2.0


class ValidationError(Exception):
    pass


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValidationError("timestamp must be non-empty string")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(dt.timezone.utc)


def default_status_dir() -> Path:
    if os.environ.get("AGENT_STATUS_DIR"):
        return Path(os.environ["AGENT_STATUS_DIR"]).expanduser()
    if os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"]).expanduser()
    else:
        base = Path.home() / ".local" / "state"
    return base / "agent-status"


def status_file_path(agent_id: str, status_dir: Path | None = None) -> Path:
    if not agent_id or "/" in agent_id or "\\" in agent_id:
        raise ValidationError("agent_id must be non-empty and filename-safe")
    return (status_dir or default_status_dir()) / f"{agent_id}.json"


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "agent_id": args.agent_id,
        "agent_name": args.agent_name,
        "runtime": {
            "lifecycle": args.lifecycle,
            "updated_at": args.updated_at or now_utc(),
        },
    }
    runtime = payload["runtime"]
    if args.last_activity_at:
        runtime["last_activity_at"] = args.last_activity_at
    if args.pid is not None:
        runtime["pid"] = args.pid
    if args.workspace:
        runtime["workspace"] = str(Path(args.workspace).expanduser().resolve())

    task_present = any(
        value is not None
        for value in [args.task_id, args.context_id, args.task_state, args.task_summary, args.task_status_timestamp]
    )
    if task_present:
        task: dict[str, Any] = {}
        if args.task_id is not None:
            task["id"] = args.task_id
        if args.context_id is not None:
            task["context_id"] = args.context_id
        if args.task_state is not None:
            task["state"] = args.task_state
        if args.task_summary is not None:
            task["summary"] = args.task_summary
        if args.task_status_timestamp is not None:
            task["status_timestamp"] = args.task_status_timestamp
        payload["task"] = task

    a2a_present = any(value is not None for value in [args.agent_card_url, args.service_url])
    if a2a_present:
        a2a: dict[str, Any] = {}
        if args.agent_card_url is not None:
            a2a["agent_card_url"] = args.agent_card_url
        if args.service_url is not None:
            a2a["service_url"] = args.service_url
        payload["a2a"] = a2a

    if args.meta:
        meta: dict[str, str] = {}
        for item in args.meta:
            if "=" not in item:
                raise ValidationError(f"invalid meta, expected key=value: {item}")
            key, value = item.split("=", 1)
            if not key:
                raise ValidationError(f"invalid meta key: {item}")
            meta[key] = value
        payload["x_meta"] = meta

    return payload


def validate_payload(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be object"]

    def require_string(obj: dict[str, Any], key: str, path: str) -> Any:
        value = obj.get(key)
        if value is None:
            errors.append(f"missing required field: {path}")
            return None
        if not isinstance(value, str) or not value:
            errors.append(f"field must be non-empty string: {path}")
            return None
        return value

    schema_version = require_string(payload, "schema_version", "schema_version")
    if schema_version and schema_version != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {schema_version}")

    require_string(payload, "agent_id", "agent_id")
    require_string(payload, "agent_name", "agent_name")

    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        errors.append("missing or invalid object: runtime")
        runtime = None
    if runtime:
        lifecycle = require_string(runtime, "lifecycle", "runtime.lifecycle")
        if lifecycle and lifecycle not in RUNTIME_LIFECYCLES:
            errors.append(f"invalid runtime.lifecycle: {lifecycle}")
        updated_at = require_string(runtime, "updated_at", "runtime.updated_at")
        if updated_at:
            try:
                parse_timestamp(updated_at)
            except ValidationError as exc:
                errors.append(str(exc))
        for field in ["last_activity_at"]:
            if field in runtime:
                try:
                    parse_timestamp(runtime[field])
                except ValidationError as exc:
                    errors.append(f"runtime.{field}: {exc}")
        if "pid" in runtime and not isinstance(runtime["pid"], int):
            errors.append("runtime.pid must be integer")
        if "workspace" in runtime and not isinstance(runtime["workspace"], str):
            errors.append("runtime.workspace must be string")

    task = payload.get("task")
    if task is not None:
        if not isinstance(task, dict):
            errors.append("task must be object")
        else:
            state = task.get("state")
            if state is not None:
                if not isinstance(state, str) or not state:
                    errors.append("task.state must be non-empty string")
                elif state not in TASK_STATES:
                    errors.append(f"invalid task.state: {state}")
            for key in ["id", "context_id", "summary"]:
                if key in task and not isinstance(task[key], str):
                    errors.append(f"task.{key} must be string")
            if "status_timestamp" in task:
                try:
                    parse_timestamp(task["status_timestamp"])
                except ValidationError as exc:
                    errors.append(f"task.status_timestamp: {exc}")

    a2a = payload.get("a2a")
    if a2a is not None:
        if not isinstance(a2a, dict):
            errors.append("a2a must be object")
        else:
            for key in ["agent_card_url", "service_url"]:
                if key in a2a and not isinstance(a2a[key], str):
                    errors.append(f"a2a.{key} must be string")

    x_meta = payload.get("x_meta")
    if x_meta is not None and not isinstance(x_meta, dict):
        errors.append("x_meta must be object")

    return errors


def validate_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [f"invalid JSON: {exc}"]
    return validate_payload(payload)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def load_status_file(path: Path, warnings: list[str] | None = None) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        if warnings is not None:
            warnings.append(f"warning: ignored invalid JSON {path}: {exc}")
        return None
    errors = validate_payload(payload)
    if errors:
        if warnings is not None:
            warnings.append(f"warning: ignored invalid status {path}: {'; '.join(errors)}")
        return None
    payload["_path"] = str(path)
    return payload


def derive_state(record: dict[str, Any], now: dt.datetime | None = None, stale_after: int = DEFAULT_STALE_AFTER) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    runtime = record["runtime"]
    updated_at = parse_timestamp(runtime["updated_at"])
    if (now - updated_at).total_seconds() > stale_after:
        return "stale"
    if runtime["lifecycle"] == "running" and not record.get("task"):
        return "idle"
    task = record.get("task") or {}
    return task.get("state") or runtime["lifecycle"]


def load_status_dir(status_dir: Path, warnings: list[str] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not status_dir.exists():
        return records
    for path in sorted(status_dir.glob("*.json")):
        record = load_status_file(path, warnings=warnings)
        if record is not None:
            records.append(record)
    records.sort(key=lambda item: parse_timestamp(item["runtime"]["updated_at"]), reverse=True)
    return records


def print_list(records: list[dict[str, Any]], stale_after: int = DEFAULT_STALE_AFTER) -> None:
    rows = [["AGENT_ID", "NAME", "LIFECYCLE", "STATE", "UPDATED_AT", "WORKSPACE"]]
    now = dt.datetime.now(dt.timezone.utc)
    for record in records:
        runtime = record["runtime"]
        rows.append(
            [
                record["agent_id"],
                record["agent_name"],
                runtime["lifecycle"],
                derive_state(record, now=now, stale_after=stale_after),
                runtime["updated_at"],
                runtime.get("workspace", ""),
            ]
        )

    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def cmd_emit(args: argparse.Namespace) -> int:
    payload = build_payload(args)
    errors = validate_payload(payload)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    path = status_file_path(args.agent_id, Path(args.status_dir).expanduser())
    atomic_write_json(path, payload)
    print(path)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    warnings: list[str] = []
    records = load_status_dir(Path(args.status_dir).expanduser(), warnings=warnings)
    for warning in warnings:
        print(warning, file=sys.stderr)
    print_list(records, stale_after=args.stale_after)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    path = status_file_path(args.agent_id, Path(args.status_dir).expanduser())
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1
    payload = load_status_file(path)
    if payload is None:
        print(f"invalid status file: {path}", file=sys.stderr)
        return 1
    payload.pop("_path", None)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    status_dir = Path(args.status_dir).expanduser()
    try:
        while True:
            warnings: list[str] = []
            records = load_status_dir(status_dir, warnings=warnings)
            print("\x1b[2J\x1b[H", end="")
            print_list(records, stale_after=args.stale_after)
            for warning in warnings:
                print(warning, file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_validate(args: argparse.Namespace) -> int:
    errors = validate_file(Path(args.file))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("valid")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-status", description="Local agent status reference CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit = subparsers.add_parser("emit", help="Write status snapshot")
    emit.add_argument("--agent-id", required=True)
    emit.add_argument("--agent-name", required=True)
    emit.add_argument("--lifecycle", required=True, choices=sorted(RUNTIME_LIFECYCLES))
    emit.add_argument("--workspace")
    emit.add_argument("--pid", type=int)
    emit.add_argument("--updated-at")
    emit.add_argument("--last-activity-at")
    emit.add_argument("--task-id")
    emit.add_argument("--context-id")
    emit.add_argument("--task-state", choices=sorted(TASK_STATES))
    emit.add_argument("--task-summary")
    emit.add_argument("--task-status-timestamp")
    emit.add_argument("--agent-card-url")
    emit.add_argument("--service-url")
    emit.add_argument("--meta", action="append", default=[])
    emit.add_argument("--status-dir", default=str(default_status_dir()))
    emit.set_defaults(func=cmd_emit)

    list_cmd = subparsers.add_parser("list", help="List status snapshots")
    list_cmd.add_argument("--status-dir", default=str(default_status_dir()))
    list_cmd.add_argument("--stale-after", type=int, default=DEFAULT_STALE_AFTER)
    list_cmd.set_defaults(func=cmd_list)

    get = subparsers.add_parser("get", help="Get one status snapshot")
    get.add_argument("agent_id")
    get.add_argument("--status-dir", default=str(default_status_dir()))
    get.set_defaults(func=cmd_get)

    watch = subparsers.add_parser("watch", help="Poll and render status snapshots")
    watch.add_argument("--status-dir", default=str(default_status_dir()))
    watch.add_argument("--stale-after", type=int, default=DEFAULT_STALE_AFTER)
    watch.add_argument("--interval", type=float, default=DEFAULT_WATCH_INTERVAL)
    watch.set_defaults(func=cmd_watch)

    validate = subparsers.add_parser("validate", help="Validate status file")
    validate.add_argument("file")
    validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
