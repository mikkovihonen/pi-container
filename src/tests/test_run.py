"""
Unit tests for src/run.py — mitmweb flow export.

Run with:
    python -m pytest src/tests/test_run.py -v
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run

# Filename produced by export_mitmweb_flows: millisecond-precision, UTC, no colons.
_TS_FILENAME = re.compile(r"\d{2}-\d{2}-\d{2}-\d{3}_.+\.json")


def _make_session(sessions_dir: Path, session_id: str) -> None:
    """Write a minimal pi session .jsonl whose first line carries the id."""
    session_file = sessions_dir / "workspace" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"type": "session", "id": session_id}) + "\n")


class TestExportMitmwebFlows:
    def test_creates_file_when_source_missing(self, tmp_path):
        """A missing per-IP file must still produce an (empty) session export."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        session_id = "abc123-session-uuid"
        _make_session(sessions, session_id)

        with patch.object(run, "_load_flows_from_mount", return_value=None):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out is not None
        data = json.loads(out.read_text())
        assert data["session_id"] == session_id
        assert data["flows"] == []

    def test_file_lives_under_exports_flows_bucketed_by_date(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        session_id = "the-session-id"
        _make_session(sessions, session_id)

        with patch.object(run, "_load_flows_from_mount", return_value=[{"id": "f1"}]):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        # flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.json
        assert out.parent.parent == exports / "flows"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out.parent.name)
        assert out.name.endswith(f"_{session_id}.json")
        assert re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{3}_" + re.escape(session_id) + r"\.json", out.name)

    def test_reads_flows_for_client_ip(self, tmp_path):
        """The client IP selects its own flows-<ip>.jsonl file."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        # Two agents' files present; only the matching client IP's is read.
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "mine"}\n')
        (exports / "flows-10.0.0.9.jsonl").write_text('{"id": "theirs"}\n')

        out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert json.loads(out.read_text())["flows"] == [{"id": "mine"}]

    def test_snapshot_and_raw_share_the_project_exports_dir(self, tmp_path):
        """Raw staging and the session snapshot both live in the one per-project
        exports dir; the consumed raw file is removed after the snapshot."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"  # PROJECT_DIR/.pi-container/exports in prod
        exports.mkdir()
        _make_session(sessions, "sid")
        raw_file = exports / "flows-10.0.0.5.jsonl"
        raw_file.write_text('{"id": "f1"}\n')

        result = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        # Snapshot lands under the same exports dir the raw file was read from.
        assert result.parent.parent == exports / "flows"
        assert json.loads(result.read_text())["flows"] == [{"id": "f1"}]
        # The consumed raw file is removed once the snapshot is written.
        assert not raw_file.exists()

    def test_default_exports_dir_is_per_project(self, tmp_path, monkeypatch):
        """With no exports_dir given, it defaults to PROJECT_DIR/.pi-container/exports."""
        sessions = tmp_path / "sessions"
        _make_session(sessions, "sid")
        monkeypatch.setattr(run, "PROJECT_DIR", tmp_path)

        with patch.object(run, "_load_flows_from_mount", return_value=None):
            out = run.export_mitmweb_flows(sessions_dir=sessions, client_ips=["10.0.0.5"])

        assert out.parent.parent == tmp_path / ".pi-container" / "exports" / "flows"

    def test_ipv6_client_ip_sanitized_to_filename(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        (exports / "flows-fd00--2.jsonl").write_text('{"id": "v6"}\n')

        out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["fd00::2"])

        assert json.loads(out.read_text())["flows"] == [{"id": "v6"}]

    def test_filename_carries_timestamp(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        _make_session(sessions, "sid")

        with patch.object(run, "_load_flows_from_mount", return_value=[]):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert _TS_FILENAME.fullmatch(out.name), out.name
        assert ":" not in out.name  # filename-safe on all platforms

    def test_writes_captured_flows(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        session_id = "sid"
        _make_session(sessions, session_id)
        flows = [{"id": "f1"}, {"id": "f2"}]

        with patch.object(run, "_load_flows_from_mount", return_value=flows):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        data = json.loads(out.read_text())
        assert data["flows"] == flows
        assert data["session_id"] == session_id

    def test_no_session_files_skips(self, tmp_path):
        """With no pi session at all there is no id to key the directory on."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        sessions.mkdir()

        with patch.object(run, "_load_flows_from_mount", return_value=[{"id": "f1"}]):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out is None

    def test_dual_stack_merges_both_families_sorted(self, tmp_path):
        """A dual-stack agent's IPv4 and IPv6 files are merged, ordered by time."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        (exports / "flows-10.0.0.5.jsonl").write_text(
            '{"id": "v4-b", "timestamp_start": 20}\n{"id": "v4-a", "timestamp_start": 10}\n'
        )
        (exports / "flows-fd00--2.jsonl").write_text('{"id": "v6", "timestamp_start": 15}\n')

        out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5", "fd00::2"])

        ids = [f["id"] for f in json.loads(out.read_text())["flows"]]
        assert ids == ["v4-a", "v6", "v4-b"]  # merged and sorted by timestamp_start

    def test_consumed_raw_files_deleted_after_export(self, tmp_path):
        """Once flows are stored in the session snapshot, the raw files are gone."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        v4 = exports / "flows-10.0.0.5.jsonl"
        v6 = exports / "flows-fd00--2.jsonl"
        v4.write_text('{"id": "a"}\n')
        v6.write_text('{"id": "b"}\n')

        out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5", "fd00::2"])

        assert out.exists()  # snapshot written
        assert not v4.exists() and not v6.exists()  # raw files removed

    def test_raw_file_kept_when_write_fails(self, tmp_path):
        """A failed snapshot write must not delete the raw file (no data loss)."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        raw = exports / "flows-10.0.0.5.jsonl"
        raw.write_text('{"id": "a"}\n')

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            out = run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out is None
        assert raw.exists()  # kept

    def test_unconsumed_files_not_deleted(self, tmp_path):
        """Ambiguous unknown-IP case reads nothing, so it deletes nothing."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        a = exports / "flows-10.0.0.7.jsonl"
        b = exports / "flows-10.0.0.8.jsonl"
        a.write_text('{"id": "a"}\n')
        b.write_text('{"id": "b"}\n')

        run.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=None)

        assert a.exists() and b.exists()  # nothing attributed → nothing removed


class TestResolveFlowsFilenames:
    def test_uses_client_ips_when_known(self, tmp_path):
        assert run._resolve_flows_filenames(tmp_path, ["10.0.0.5"]) == ["flows-10.0.0.5.jsonl"]

    def test_dual_stack_returns_both(self, tmp_path):
        assert run._resolve_flows_filenames(tmp_path, ["10.0.0.5", "fd00::2"]) == [
            "flows-10.0.0.5.jsonl",
            "flows-fd00--2.jsonl",
        ]

    def test_sanitizes_ipv6(self, tmp_path):
        assert run._resolve_flows_filenames(tmp_path, ["fd00::2"]) == ["flows-fd00--2.jsonl"]

    def test_unknown_ip_uses_sole_file(self, tmp_path):
        (tmp_path / "flows-10.0.0.7.jsonl").write_text("")
        assert run._resolve_flows_filenames(tmp_path, None) == ["flows-10.0.0.7.jsonl"]

    def test_unknown_ip_ambiguous_returns_empty(self, tmp_path):
        (tmp_path / "flows-10.0.0.7.jsonl").write_text("")
        (tmp_path / "flows-10.0.0.8.jsonl").write_text("")
        assert run._resolve_flows_filenames(tmp_path, None) == []

    def test_unknown_ip_no_files_returns_empty(self, tmp_path):
        assert run._resolve_flows_filenames(tmp_path, None) == []


class TestSanitizeIp:
    def test_ipv4_unchanged(self):
        assert run._sanitize_ip("10.0.0.5") == "10.0.0.5"

    def test_ipv6_colons_become_dashes(self):
        assert run._sanitize_ip("fd00::2") == "fd00--2"

    def test_strips_brackets(self):
        assert run._sanitize_ip("[fd00::2]") == "fd00--2"


class TestLoadFlowsFromMount:
    def test_reads_jsonl_flows(self, tmp_path):
        (tmp_path / "flows-10.0.0.5.jsonl").write_text(
            '{"id": "f1", "request": {"url": "http://a/"}}\n{"id": "f2", "error": "Connection killed."}\n'
        )
        flows = run._load_flows_from_mount(exports_dir=tmp_path, flows_filename="flows-10.0.0.5.jsonl")
        assert [f["id"] for f in flows] == ["f1", "f2"]

    def test_skips_blank_and_malformed_lines(self, tmp_path):
        # blank lines and a truncated final line (as a hard kill can leave)
        (tmp_path / "flows.jsonl").write_text(
            '{"id": "f1"}\n\n{"id": "f2"}\n{"id": "partial", "request": {'  # truncated, no newline
        )
        flows = run._load_flows_from_mount(exports_dir=tmp_path)
        assert [f["id"] for f in flows] == ["f1", "f2"]

    def test_missing_file_returns_none(self, tmp_path):
        assert run._load_flows_from_mount(exports_dir=tmp_path) is None

    def test_empty_file_returns_empty_list(self, tmp_path):
        (tmp_path / "flows.jsonl").write_text("")
        assert run._load_flows_from_mount(exports_dir=tmp_path) == []


class TestProjectScope:
    def test_stable_for_same_dir(self, tmp_path):
        assert run._project_scope(tmp_path) == run._project_scope(tmp_path)

    def test_differs_across_dirs(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert run._project_scope(a) != run._project_scope(b)

    def test_name_format(self, tmp_path):
        proxy_name, network_name = run._project_scope(tmp_path)
        assert proxy_name.startswith("pi-proxy-")
        assert network_name.startswith("pi-isolated-net-")
        # Shared 10-hex-char project key across both names.
        assert proxy_name.split("pi-proxy-")[1] == network_name.split("pi-isolated-net-")[1]
        assert re.fullmatch(r"[0-9a-f]{10}", proxy_name.split("pi-proxy-")[1])


class TestEnsureProjectConfig:
    def _make_template(self, root):
        """Build a minimal pi-coding-agent/default template under root."""
        template = root / "pi-coding-agent" / "default"
        (template / "agent").mkdir(parents=True)
        (template / "agent" / "models.json").write_text("{}")
        (template / "allowlist.yaml").write_text("global: {}\n")
        (template / "token_replacer.yaml").write_text("global: {}\n")
        (template / "tmpfs.yaml").write_text("paths: []\n")
        return template

    def test_seeds_agent_and_yaml_when_absent(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        project.mkdir()
        self._make_template(repo)
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        agent_dir = run._ensure_project_config()

        assert agent_dir == project / ".pi-container" / "agent"
        assert (agent_dir / "models.json").exists()
        for name in ("allowlist.yaml", "token_replacer.yaml", "tmpfs.yaml"):
            assert (project / ".pi-container" / name).exists()

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        project = tmp_path / "project"
        self._make_template(repo)
        # Pre-existing, user-edited allowlist must be preserved.
        existing = project / ".pi-container" / "allowlist.yaml"
        existing.parent.mkdir(parents=True)
        existing.write_text("global: {custom: true}\n")
        monkeypatch.setattr(run, "REPO_ROOT", repo)
        monkeypatch.setattr(run, "PROJECT_DIR", project)

        run._ensure_project_config()

        assert existing.read_text() == "global: {custom: true}\n"
        # Missing ones are still seeded.
        assert (project / ".pi-container" / "token_replacer.yaml").exists()
        assert (project / ".pi-container" / "agent" / "models.json").exists()
