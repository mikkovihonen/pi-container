import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_ADDON_PATH = Path(__file__).resolve().parent.parent / "flow_export.py"
_SPEC = importlib.util.spec_from_file_location("proxy_flow_export_addon", _ADDON_PATH)
_PROXY_FLOW_EXPORT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROXY_FLOW_EXPORT)
FlowExporter = _PROXY_FLOW_EXPORT.FlowExporter


class TestFlowExporterDeniedFlows:
    def test_denied_flow_status_403(self):
        exporter = FlowExporter()
        flow = MagicMock()
        flow.response.status_code = 403
        flow.error = None
        flow.marked = False
        assert exporter._is_denied_flow(flow) is True

    def test_denied_flow_status_444(self):
        exporter = FlowExporter()
        flow = MagicMock()
        flow.response.status_code = 444
        flow.error = None
        flow.marked = False
        assert exporter._is_denied_flow(flow) is True

    def test_denied_flow_with_error(self):
        exporter = FlowExporter()
        flow = MagicMock()
        flow.response = None
        flow.error = "Connection killed"
        flow.marked = False
        assert exporter._is_denied_flow(flow) is True

    def test_denied_flow_marked(self):
        exporter = FlowExporter()
        flow = MagicMock()
        flow.response.status_code = 200
        flow.error = None
        flow.marked = True
        assert exporter._is_denied_flow(flow) is True

    def test_allowed_flow_not_denied(self):
        exporter = FlowExporter()
        flow = MagicMock()
        flow.response.status_code = 200
        flow.error = None
        flow.marked = False
        assert exporter._is_denied_flow(flow) is False


class TestFlowExporterViewTrimming:
    def test_trims_only_allowed_flows(self, monkeypatch):
        exporter = FlowExporter()
        exporter._max_view_flows = 2

        # Create 4 mock flows: 2 allowed, 2 denied (403 and error)
        flow_allowed_1 = MagicMock()
        flow_allowed_1.response.status_code = 200
        flow_allowed_1.error = None
        flow_allowed_1.marked = False

        flow_denied_1 = MagicMock()
        flow_denied_1.response.status_code = 403
        flow_denied_1.error = None
        flow_denied_1.marked = False

        flow_allowed_2 = MagicMock()
        flow_allowed_2.response.status_code = 200
        flow_allowed_2.error = None
        flow_allowed_2.marked = False

        flow_denied_2 = MagicMock()
        flow_denied_2.response = None
        flow_denied_2.error = "Killed"
        flow_denied_2.marked = False

        mock_view = MagicMock()
        flows_in_view = [flow_allowed_1, flow_denied_1, flow_allowed_2, flow_denied_2]
        mock_view.__iter__ = MagicMock(return_value=iter(flows_in_view))
        mock_view.__len__ = MagicMock(return_value=len(flows_in_view))
        removed_flows = []
        mock_view.remove = MagicMock(side_effect=lambda to_rem: removed_flows.extend(to_rem))

        mock_master = MagicMock()
        mock_master.addons.get.return_value = mock_view

        mock_ctx = MagicMock()
        mock_ctx.master = mock_master
        monkeypatch.setattr("mitmproxy.ctx", mock_ctx, raising=False)

        exporter._trim_in_memory_view()

        # Total is 4, max is 2 -> excess is 2.
        # It should only remove flow_allowed_1 and flow_allowed_2, preserving both denied flows.
        assert flow_allowed_1 in removed_flows
        assert flow_allowed_2 in removed_flows
        assert flow_denied_1 not in removed_flows
        assert flow_denied_2 not in removed_flows

    def test_no_trimming_when_under_limit(self, monkeypatch):
        exporter = FlowExporter()
        exporter._max_view_flows = 5

        mock_view = MagicMock()
        mock_view.__len__ = MagicMock(return_value=3)
        mock_view.remove = MagicMock()

        mock_master = MagicMock()
        mock_master.addons.get.return_value = mock_view

        mock_ctx = MagicMock()
        mock_ctx.master = mock_master
        monkeypatch.setattr("mitmproxy.ctx", mock_ctx, raising=False)

        exporter._trim_in_memory_view()
        mock_view.remove.assert_not_called()
