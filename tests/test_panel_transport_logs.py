"""Current QQ transport log selection in the panel."""
from tests import _env  # noqa: F401

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from panel.services import logs as panel_logs


class PanelTransportLogsTest(unittest.TestCase):
    def test_transport_name_reflects_selected_provider(self):
        with patch.object(panel_logs.qq_transport, "uses_snowluma", return_value=True):
            self.assertEqual(panel_logs.transport_name(), "SnowLuma")
        with patch.object(panel_logs.qq_transport, "uses_snowluma", return_value=False):
            self.assertEqual(panel_logs.transport_name(), "NapCat")

    def test_latest_transport_log_tracks_the_selected_transport_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            log_dir.mkdir()
            older = log_dir / "snowluma-2026-10-30.log"
            newer = log_dir / "snowluma-2026-10-31.log"
            older.write_text("old\n", encoding="utf-8")
            newer.write_text("new\n", encoding="utf-8")
            os.utime(older, (1_800_000_000, 1_800_000_000))
            os.utime(newer, (1_800_000_100, 1_800_000_100))
            with patch.object(panel_logs.napcat, "log_dir", return_value=log_dir):
                self.assertEqual(panel_logs.latest_transport_log(), newer)

    def test_latest_transport_log_returns_none_when_no_log_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(panel_logs.napcat, "log_dir", return_value=Path(tmp)):
                self.assertIsNone(panel_logs.latest_transport_log())


if __name__ == "__main__":
    unittest.main()
