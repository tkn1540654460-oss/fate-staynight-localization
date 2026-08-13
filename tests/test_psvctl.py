import tempfile
import unittest
from pathlib import Path

import psvctl


class RemotePathTests(unittest.TestCase):
    def test_accepts_common_vitashell_spelling(self):
        self.assertEqual(
            psvctl.canonical_remote("ux0:app/PCSG00001/data.bin"),
            "ux0:/app/PCSG00001/data.bin",
        )

    def test_rejects_parent_traversal(self):
        with self.assertRaises(psvctl.PsvCtlError):
            psvctl.canonical_remote("ux0:rePatch/PCSG00001/../app/file.bin")

    def test_original_game_is_read_only(self):
        self.assertEqual(
            psvctl.require_read_path("ux0:app/PCSG00001/file.bin"),
            "ux0:/app/PCSG00001/file.bin",
        )
        with self.assertRaises(psvctl.PsvCtlError):
            psvctl.require_write_path("ux0:app/PCSG00001/file.bin")

    def test_repatch_is_writable(self):
        self.assertEqual(
            psvctl.require_write_path("ux0:rePatch/PCSG00001/file.bin"),
            "ux0:/rePatch/PCSG00001/file.bin",
        )

    def test_other_volumes_are_rejected(self):
        with self.assertRaises(psvctl.PsvCtlError):
            psvctl.require_read_path("ur0:tai/config.txt")


class ConfigTests(unittest.TestCase):
    def test_load_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"host":"192.168.0.2","port":1337}', encoding="utf-8")
            self.assertEqual(psvctl.load_config(path)["port"], 1337)


if __name__ == "__main__":
    unittest.main()

