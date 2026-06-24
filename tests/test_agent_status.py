import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import agent_status


class AgentStatusTests(unittest.TestCase):
    def test_valid_minimal_record(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {
                "lifecycle": "running",
                "updated_at": "2026-06-20T16:45:00Z",
            },
        }
        self.assertEqual(agent_status.validate_payload(payload), [])

    def test_valid_full_record(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {
                "lifecycle": "running",
                "updated_at": "2026-06-20T16:45:00Z",
                "last_activity_at": "2026-06-20T16:44:52Z",
                "pid": 123,
                "workspace": "/tmp/project",
            },
            "goal": {
                "summary": "session intent",
                "updated_at": "2026-06-20T16:44:50Z",
                "source": "initial-prompt",
            },
            "task": {
                "id": "task-1",
                "context_id": "ctx-1",
                "state": "working",
                "summary": "do work",
                "status_timestamp": "2026-06-20T16:44:55Z",
            },
            "a2a": {
                "agent_card_url": "http://127.0.0.1:8711/.well-known/agent-card.json",
                "service_url": "http://127.0.0.1:8711/a2a",
            },
            "x_meta": {"branch": "main"},
        }
        self.assertEqual(agent_status.validate_payload(payload), [])

    def test_missing_required_field_fails(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_name": "pi",
            "runtime": {"lifecycle": "running"},
        }
        errors = agent_status.validate_payload(payload)
        self.assertTrue(any("agent_id" in error for error in errors))
        self.assertTrue(any("runtime.updated_at" in error for error in errors))

    def test_invalid_runtime_lifecycle_fails(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "broken", "updated_at": "2026-06-20T16:45:00Z"},
        }
        errors = agent_status.validate_payload(payload)
        self.assertTrue(any("invalid runtime.lifecycle" in error for error in errors))

    def test_invalid_goal_timestamp_fails(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
            "goal": {"summary": "intent", "updated_at": "bad", "source": "initial-prompt"},
        }
        errors = agent_status.validate_payload(payload)
        self.assertTrue(any("goal.updated_at" in error for error in errors))

    def test_partial_goal_rejected(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
            "goal": {"summary": "intent"},
        }
        errors = agent_status.validate_payload(payload)
        self.assertTrue(any("goal.updated_at" in error for error in errors))
        self.assertTrue(any("goal.source" in error for error in errors))

    def test_full_goal_accepted(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
            "goal": {
                "summary": "intent",
                "updated_at": "2026-06-20T16:44:50Z",
                "source": "initial-prompt",
            },
        }
        self.assertEqual(agent_status.validate_payload(payload), [])

    def test_invalid_task_state_fails(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
            "task": {"state": "idle"},
        }
        errors = agent_status.validate_payload(payload)
        self.assertTrue(any("invalid task.state" in error for error in errors))

    def test_idle_inference(self):
        record = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2999-06-20T16:45:00Z"},
        }
        self.assertEqual(agent_status.derive_state(record), "idle")

    def test_stale_detection(self):
        record = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {"lifecycle": "running", "updated_at": "2000-06-20T16:45:00Z"},
        }
        self.assertEqual(agent_status.derive_state(record, stale_after=60), "stale")

    def test_atomic_write_output_parseable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pi-1.json"
            payload = {
                "schema_version": agent_status.SCHEMA_VERSION,
                "agent_id": "pi-1",
                "agent_name": "pi",
                "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
            }
            for index in range(10):
                payload["runtime"]["updated_at"] = f"2026-06-20T16:45:{index:02d}Z"
                agent_status.atomic_write_json(path, payload)
                loaded = json.loads(path.read_text())
                self.assertEqual(loaded["runtime"]["updated_at"], payload["runtime"]["updated_at"])

    def test_list_ordering_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            older = Path(tmp) / "older.json"
            newer = Path(tmp) / "newer.json"
            agent_status.atomic_write_json(
                older,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "older",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
                },
            )
            agent_status.atomic_write_json(
                newer,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "newer",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:46:00Z"},
                },
            )
            records = agent_status.load_status_dir(Path(tmp))
            self.assertEqual([record["agent_id"] for record in records], ["newer", "older"])

    def test_unknown_extra_fields_tolerated(self):
        payload = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {
                "lifecycle": "running",
                "updated_at": "2026-06-20T16:45:00Z",
                "x_future": True,
            },
            "x_future_top": {"anything": [1, 2, 3]},
        }
        self.assertEqual(agent_status.validate_payload(payload), [])

    def test_display_sort_key_buckets_and_recency(self):
        now = agent_status.parse_timestamp("2026-06-20T16:50:00Z")
        stale_after = 120

        def record(agent_id, updated_at, lifecycle="running", task_state=None):
            item = {
                "schema_version": agent_status.SCHEMA_VERSION,
                "agent_id": agent_id,
                "agent_name": "pi",
                "runtime": {"lifecycle": lifecycle, "updated_at": updated_at},
            }
            if task_state is not None:
                item["task"] = {"state": task_state}
            return item

        records = [
            record("idle-newer", "2026-06-20T16:49:00Z"),
            record("working-older", "2026-06-20T16:48:00Z", task_state="working"),
            record("working-newer", "2026-06-20T16:49:30Z", task_state="working"),
            record("stopped", "2026-06-20T16:49:45Z", lifecycle="stopped"),
            record("stale", "2026-06-20T16:47:30Z"),
        ]

        ordered = sorted(
            records,
            key=lambda rec: agent_status.display_sort_key(rec, now=now, stale_after=stale_after),
        )

        self.assertEqual(
            [record["agent_id"] for record in ordered],
            ["working-newer", "working-older", "stopped", "idle-newer", "stale"],
        )

    def test_idle_sort_uses_last_activity_at(self):
        """Idle agents sort by last_activity_at, not heartbeat updated_at."""
        now = agent_status.parse_timestamp("2026-06-20T16:50:00Z")
        stale_after = 600

        def idle_record(agent_id, updated_at, last_activity_at):
            return {
                "schema_version": agent_status.SCHEMA_VERSION,
                "agent_id": agent_id,
                "agent_name": "pi",
                "runtime": {
                    "lifecycle": "running",
                    "updated_at": updated_at,
                    "last_activity_at": last_activity_at,
                },
            }

        # Agent A: recent heartbeat but old activity
        # Agent B: older heartbeat but recent activity
        records = [
            idle_record("agent-a", "2026-06-20T16:49:50Z", "2026-06-20T16:40:00Z"),
            idle_record("agent-b", "2026-06-20T16:49:00Z", "2026-06-20T16:48:00Z"),
        ]

        ordered = sorted(
            records,
            key=lambda rec: agent_status.display_sort_key(rec, now=now, stale_after=stale_after),
        )
        # Agent B had more recent activity, should appear first despite older heartbeat
        self.assertEqual([r["agent_id"] for r in ordered], ["agent-b", "agent-a"])

    def test_idle_sort_stable_tie_break_by_agent_id(self):
        """Idle agents with equal timestamps sort stably by agent_id."""
        now = agent_status.parse_timestamp("2026-06-20T16:50:00Z")
        stale_after = 600

        def idle_record(agent_id, updated_at, last_activity_at):
            return {
                "schema_version": agent_status.SCHEMA_VERSION,
                "agent_id": agent_id,
                "agent_name": "pi",
                "runtime": {
                    "lifecycle": "running",
                    "updated_at": updated_at,
                    "last_activity_at": last_activity_at,
                },
            }

        records = [
            idle_record("zz-agent", "2026-06-20T16:49:00Z", "2026-06-20T16:48:00Z"),
            idle_record("aa-agent", "2026-06-20T16:49:00Z", "2026-06-20T16:48:00Z"),
            idle_record("mm-agent", "2026-06-20T16:49:00Z", "2026-06-20T16:48:00Z"),
        ]

        ordered = sorted(
            records,
            key=lambda rec: agent_status.display_sort_key(rec, now=now, stale_after=stale_after),
        )
        self.assertEqual(
            [r["agent_id"] for r in ordered],
            ["aa-agent", "mm-agent", "zz-agent"],
        )

    def test_invalid_json_file_ignored_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            bad = Path(tmp) / "bad.json"
            agent_status.atomic_write_json(
                good,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "pi-1",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:45:00Z"},
                },
            )
            bad.write_text("{ nope ")
            warnings = []
            records = agent_status.load_status_dir(Path(tmp), warnings=warnings)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["agent_id"], "pi-1")
            self.assertEqual(len(warnings), 1)
            self.assertIn("ignored invalid JSON", warnings[0])

    def test_prune_removes_old_stale_and_stopped_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_dir = Path(tmp)
            stale_path = status_dir / "stale.json"
            stopped_path = status_dir / "stopped.json"
            fresh_path = status_dir / "fresh.json"
            agent_status.atomic_write_json(
                stale_path,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "stale",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-18T16:45:00Z"},
                },
            )
            agent_status.atomic_write_json(
                stopped_path,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "stopped",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "stopped", "updated_at": "2026-06-18T16:45:00Z"},
                },
            )
            agent_status.atomic_write_json(
                fresh_path,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "fresh",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-20T16:44:45Z"},
                },
            )

            removed = agent_status.prune_status_dir(
                status_dir,
                now=agent_status.parse_timestamp("2026-06-20T16:45:00Z"),
                stale_after=60,
                prune_after=3600,
            )

            self.assertEqual({path.name for path in removed}, {"stale.json", "stopped.json"})
            self.assertFalse(stale_path.exists())
            self.assertFalse(stopped_path.exists())
            self.assertTrue(fresh_path.exists())

    def test_print_list_shows_goal_when_task_missing(self):
        try:
            import rich  # noqa: F401
        except ImportError:
            self.skipTest("rich not installed")
        record = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {
                "lifecycle": "running",
                "updated_at": "2999-06-20T16:45:00Z",
            },
            "goal": {
                "summary": "durable session intent",
                "updated_at": "2999-06-20T16:44:00Z",
                "source": "initial-prompt",
            },
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent_status.print_list([record], stale_after=60, color=False)

        output = buffer.getvalue()
        self.assertIn("durable session intent", output)
        self.assertIn("idle", output)

    def test_print_list_aligns_columns_without_tabs(self):
        try:
            import rich
        except ImportError:
            self.skipTest("rich not installed")
        record = {
            "schema_version": agent_status.SCHEMA_VERSION,
            "agent_id": "pi-1",
            "agent_name": "pi",
            "runtime": {
                "lifecycle": "running",
                "updated_at": "2000-06-20T16:45:00Z",
                "workspace": "/tmp/project",
                "pid": 123,
            },
            "goal": {"summary": "durable session intent", "updated_at": "2000-06-20T16:44:00Z", "source": "initial-prompt"},
            "task": {"state": "working", "summary": "current task line"},
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent_status.print_list([record], stale_after=60, color=False)

        output = buffer.getvalue()
        self.assertNotIn("\t", output)
        self.assertNotIn("pid 123", output)
        self.assertIn("◎", output)
        self.assertIn("📁", output)
        self.assertIn("▸", output)
        self.assertIn("📁  /tmp/project", output)
        goal_pos = output.index("◎")
        ws_pos = output.index("📁  /tmp/project")
        task_pos = output.index("▸")
        self.assertLess(goal_pos, ws_pos)
        self.assertLess(ws_pos, task_pos)

    def test_cli_emit_incomplete_goal_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_dir = Path(tmp)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = agent_status.main([
                    "emit",
                    "--status-dir",
                    str(status_dir),
                    "--agent-id",
                    "pi-1",
                    "--agent-name",
                    "pi",
                    "--lifecycle",
                    "running",
                    "--goal-summary",
                    "ship feature",
                ])
            self.assertEqual(exit_code, 1)
            self.assertIn("goal.updated_at", stderr.getvalue())
            self.assertIn("goal.source", stderr.getvalue())
            self.assertFalse((status_dir / "pi-1.json").exists())

    def test_cli_get_validate_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_dir = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = agent_status.main([
                    "emit",
                    "--status-dir",
                    str(status_dir),
                    "--agent-id",
                    "pi-1",
                    "--agent-name",
                    "pi",
                    "--lifecycle",
                    "running",
                    "--goal-summary",
                    "ship feature",
                    "--goal-updated-at",
                    "2026-06-20T16:44:50Z",
                    "--goal-source",
                    "initial-prompt",
                ])
            self.assertEqual(exit_code, 0)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                exit_code = agent_status.main([
                    "get",
                    "pi-1",
                    "--status-dir",
                    str(status_dir),
                ])
            self.assertEqual(exit_code, 0)
            output = json.loads(buffer.getvalue())
            self.assertEqual(output["agent_id"], "pi-1")
            self.assertEqual(output["goal"]["summary"], "ship feature")
            file_path = status_dir / "pi-1.json"
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = agent_status.main(["validate", str(file_path)])
            self.assertEqual(exit_code, 0)

            agent_status.atomic_write_json(
                file_path,
                {
                    "schema_version": agent_status.SCHEMA_VERSION,
                    "agent_id": "pi-1",
                    "agent_name": "pi",
                    "runtime": {"lifecycle": "running", "updated_at": "2026-06-18T16:45:00Z"},
                },
            )
            prune_buffer = io.StringIO()
            with contextlib.redirect_stdout(prune_buffer):
                exit_code = agent_status.main([
                    "prune",
                    "--status-dir",
                    str(status_dir),
                    "--prune-after",
                    "3600",
                ])
            self.assertEqual(exit_code, 0)
            self.assertIn("pi-1.json", prune_buffer.getvalue())
            self.assertFalse(file_path.exists())

    def test_state_color_mapping(self):
        self.assertEqual(agent_status._STATE_COLOR["input-required"], "yellow")
        self.assertEqual(agent_status._STATE_COLOR["submitted"], "cyan")
        self.assertNotEqual(agent_status._STATE_COLOR["input-required"], agent_status._STATE_COLOR["submitted"])

    def test_human_age(self):
        now = agent_status.parse_timestamp("2026-06-22T12:00:00Z")
        self.assertEqual(agent_status._human_age("2026-06-22T11:59:50Z", now), "10s ago")
        self.assertEqual(agent_status._human_age("2026-06-22T11:58:00Z", now), "2m ago")
        self.assertEqual(agent_status._human_age("2026-06-22T10:00:00Z", now), "2h ago")
        self.assertEqual(agent_status._human_age("2026-06-20T12:00:00Z", now), "2d ago")


if __name__ == "__main__":
    unittest.main()
