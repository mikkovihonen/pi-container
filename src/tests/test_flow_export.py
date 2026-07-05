"""
Unit tests for src/flow_export.py — host-side mitmweb flow export.

Run with:
    python -m pytest src/tests/test_flow_export.py -v
"""

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flow_export

# Filename produced by export_mitmweb_flows: millisecond-precision, UTC, no colons.
_TS_FILENAME = re.compile(r"\d{2}-\d{2}-\d{2}-\d{3}_.+\.jsonl")


def _make_session(sessions_dir: Path, session_id: str) -> None:
    """Write a minimal pi session .jsonl whose first line carries the id."""
    session_file = sessions_dir / "workspace" / "session.jsonl"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(json.dumps({"type": "session", "id": session_id}) + "\n")


class TestExportMitmwebFlows:
    def test_creates_file_when_source_missing(self, tmp_path):
        """A missing per-IP file means nothing to copy, so we skip silently."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        session_id = "abc123-session-uuid"
        _make_session(sessions, session_id)

        # No raw flows file exists on the mount.
        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out is None
        # No export directory was created either — nothing to do.
        assert not (exports / "flows").exists()

    def test_file_lives_under_exports_flows_bucketed_by_date(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        session_id = "the-session-id"
        _make_session(sessions, session_id)
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "f1"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        # flows/<YYYY-MM-DD>/<HH-MM-SS-mmm>_<session-id>.jsonl
        assert out.parent.parent == exports / "flows"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", out.parent.name)
        assert out.name.endswith(f"_{session_id}.jsonl")
        assert re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{3}_" + re.escape(session_id) + r"\.jsonl", out.name)

    def test_reads_flows_for_client_ip(self, tmp_path):
        """The client IP selects its own flows-<ip>.jsonl file."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        # Two agents' files present; only the matching client IP's is read.
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "mine"}\n')
        (exports / "flows-10.0.0.9.jsonl").write_text('{"id": "theirs"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        # Raw JSONL preserved as-is — no wrapper object, no parsing.
        assert out.read_text() == '{"id": "mine"}\n'
        # The consumed raw file is gone.
        assert not (exports / "flows-10.0.0.5.jsonl").exists()
        # The unattributed file remains.
        assert (exports / "flows-10.0.0.9.jsonl").exists()

    def test_snapshot_and_raw_share_the_project_exports_dir(self, tmp_path):
        """Raw staging and the session snapshot both live in the one per-project
        exports dir; the consumed raw file is removed after the snapshot."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        raw_file = exports / "flows-10.0.0.5.jsonl"
        raw_file.write_text('{"id": "f1"}\n')

        result = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        # Snapshot lands under the same exports dir the raw file was read from.
        assert result.parent.parent == exports / "flows"
        # Content is the raw JSONL line, copied verbatim.
        assert result.read_text() == '{"id": "f1"}\n'
        # The consumed raw file is removed once the snapshot is written.
        assert not raw_file.exists()

    def test_default_exports_dir_is_per_project(self, tmp_path, monkeypatch):
        """With no exports_dir given, it defaults to PROJECT_DIR/.pi-container/exports."""
        sessions = tmp_path / "sessions"
        _make_session(sessions, "sid")
        monkeypatch.setattr(flow_export, "PROJECT_DIR", tmp_path)
        (tmp_path / ".pi-container" / "exports").mkdir(parents=True)
        (tmp_path / ".pi-container" / "exports" / "flows-10.0.0.5.jsonl").write_text('{"id": "f1"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, client_ips=["10.0.0.5"])

        assert out.parent.parent == tmp_path / ".pi-container" / "exports" / "flows"
        assert out.name.endswith(".jsonl")

    def test_ipv6_client_ip_sanitized_to_filename(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        (exports / "flows-fd00--2.jsonl").write_text('{"id": "v6"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["fd00::2"])

        assert out.read_text() == '{"id": "v6"}\n'

    def test_filename_carries_timestamp(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "f1"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert _TS_FILENAME.fullmatch(out.name), out.name
        assert ":" not in out.name  # filename-safe on all platforms

    def test_writes_captured_flows(self, tmp_path):
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        session_id = "sid"
        _make_session(sessions, session_id)
        raw = '{"id": "f1"}\n{"id": "f2"}\n'
        (exports / "flows-10.0.0.5.jsonl").write_text(raw)

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out.read_text() == raw
        assert not (exports / "flows-10.0.0.5.jsonl").exists()

    def test_no_session_files_skips(self, tmp_path):
        """With no pi session at all there is no id to key the directory on."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        sessions.mkdir()
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "f1"}\n')

        out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

        assert out is None

    def test_dual_stack_concatenates_both_families(self, tmp_path):
        """A dual-stack agent's IPv4 and IPv6 files are concatenated (preserving
        their original line order) into a single .jsonl export."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "v4-a"}\n{"id": "v4-b"}\n')
        (exports / "flows-fd00--2.jsonl").write_text('{"id": "v6"}\n')

        out = flow_export.export_mitmweb_flows(
            sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5", "fd00::2"]
        )

        lines = [line for line in out.read_text().splitlines() if line]
        assert [json.loads(line)["id"] for line in lines] == ["v4-a", "v4-b", "v6"]

    def test_consumed_raw_files_deleted_after_export(self, tmp_path):
        """Once flows are copied to the session snapshot, the raw files are gone."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        v4 = exports / "flows-10.0.0.5.jsonl"
        v6 = exports / "flows-fd00--2.jsonl"
        v4.write_text('{"id": "a"}\n')
        v6.write_text('{"id": "b"}\n')

        out = flow_export.export_mitmweb_flows(
            sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5", "fd00::2"]
        )

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

        with patch("pathlib.Path.write_bytes", side_effect=OSError("disk full")):
            out = flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5"])

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

        flow_export.export_mitmweb_flows(sessions_dir=sessions, exports_dir=exports, client_ips=None)

        assert a.exists() and b.exists()  # nothing attributed → nothing removed

    def test_concatenation_adds_newline_between_files(self, tmp_path):
        """When two files are concatenated, a newline is inserted between them so
        line boundaries don't merge across files."""
        sessions = tmp_path / "sessions"
        exports = tmp_path / "exports"
        exports.mkdir()
        _make_session(sessions, "sid")
        # Second file has no trailing newline.
        (exports / "flows-10.0.0.5.jsonl").write_text('{"id": "first"}\n')
        (exports / "flows-fd00--2.jsonl").write_text('{"id": "second"}')

        out = flow_export.export_mitmweb_flows(
            sessions_dir=sessions, exports_dir=exports, client_ips=["10.0.0.5", "fd00::2"]
        )

        lines = [line for line in out.read_text().splitlines() if line]
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == "first"
        assert json.loads(lines[1])["id"] == "second"


class TestResolveFlowsFilenames:
    def test_uses_client_ips_when_known(self, tmp_path):
        assert flow_export._resolve_flows_filenames(tmp_path, ["10.0.0.5"]) == ["flows-10.0.0.5.jsonl"]

    def test_dual_stack_returns_both(self, tmp_path):
        assert flow_export._resolve_flows_filenames(tmp_path, ["10.0.0.5", "fd00::2"]) == [
            "flows-10.0.0.5.jsonl",
            "flows-fd00--2.jsonl",
        ]

    def test_sanitizes_ipv6(self, tmp_path):
        assert flow_export._resolve_flows_filenames(tmp_path, ["fd00::2"]) == ["flows-fd00--2.jsonl"]

    def test_unknown_ip_uses_sole_file(self, tmp_path):
        (tmp_path / "flows-10.0.0.7.jsonl").write_text("")
        assert flow_export._resolve_flows_filenames(tmp_path, None) == ["flows-10.0.0.7.jsonl"]

    def test_unknown_ip_ambiguous_returns_empty(self, tmp_path):
        (tmp_path / "flows-10.0.0.7.jsonl").write_text("")
        (tmp_path / "flows-10.0.0.8.jsonl").write_text("")
        assert flow_export._resolve_flows_filenames(tmp_path, None) == []

    def test_unknown_ip_no_files_returns_empty(self, tmp_path):
        assert flow_export._resolve_flows_filenames(tmp_path, None) == []


class TestSanitizeIp:
    def test_ipv4_unchanged(self):
        assert flow_export._sanitize_ip("10.0.0.5") == "10.0.0.5"

    def test_ipv6_colons_become_dashes(self):
        assert flow_export._sanitize_ip("fd00::2") == "fd00--2"

    def test_strips_brackets(self):
        assert flow_export._sanitize_ip("[fd00::2]") == "fd00--2"


class TestLoadFlowsFromMount:
    def test_reads_jsonl_flows(self, tmp_path):
        (tmp_path / "flows-10.0.0.5.jsonl").write_text(
            '{"id": "f1", "request": {"url": "http://a/"}}\n{"id": "f2", "error": "Connection killed."}\n'
        )
        flows = flow_export._load_flows_from_mount(exports_dir=tmp_path, flows_filename="flows-10.0.0.5.jsonl")
        assert [f["id"] for f in flows] == ["f1", "f2"]

    def test_skips_blank_and_malformed_lines(self, tmp_path):
        # blank lines and a truncated final line (as a hard kill can leave)
        (tmp_path / "flows.jsonl").write_text(
            '{"id": "f1"}\n\n{"id": "f2"}\n{"id": "partial", "request": {'  # truncated, no newline
        )
        flows = flow_export._load_flows_from_mount(exports_dir=tmp_path)
        assert [f["id"] for f in flows] == ["f1", "f2"]

    def test_missing_file_returns_none(self, tmp_path):
        assert flow_export._load_flows_from_mount(exports_dir=tmp_path) is None

    def test_empty_file_returns_empty_list(self, tmp_path):
        (tmp_path / "flows.jsonl").write_text("")
        assert flow_export._load_flows_from_mount(exports_dir=tmp_path) == []
