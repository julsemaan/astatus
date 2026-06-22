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

    def test_print_list_aligns_columns_without_tabs(self):
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
                "updated_at": "2000-06-20T16:45:00Z",
                "workspace": "/tmp/project",
                "pid": 123,
            },
        }
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            agent_status.print_list([record], stale_after=60, color=False)

        output = buffer.getvalue()
        self.assertNotIn("\t", output)

        lines = output.splitlines()
        # agent row should have the stale state
        agent_lines = [l for l in lines if "stale" in l and "─" not in l[:4]]
        self.assertEqual(len(agent_lines), 1, f"expected one agent row, got {lines}")
        agent_row = agent_lines[0]
        # sub-row should contain workspace, not in agent row
        sub_lines = [l for l in lines if "/tmp/project" in l]
        self.assertEqual(len(sub_lines), 1, f"expected one sub-row with workspace, got {lines}")
        self.assertNotIn("/tmp/project", agent_row)
        # sub-row should contain pid
        self.assertIn("pid 123", sub_lines[0])

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

    def test_human_age(self):
        now = agent_status.parse_timestamp("2026-06-22T12:00:00Z")
        self.assertEqual(agent_status._human_age("2026-06-22T11:59:50Z", now), "10s ago")
        self.assertEqual(agent_status._human_age("2026-06-22T11:58:00Z", now), "2m ago")
        self.assertEqual(agent_status._human_age("2026-06-22T10:00:00Z", now), "2h ago")
        self.assertEqual(agent_status._human_age("2026-06-20T12:00:00Z", now), "2d ago")


if __name__ == "__main__":
    unittest.main()
