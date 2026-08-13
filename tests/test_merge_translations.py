import json
import tempfile
import unittest
from pathlib import Path

from tools.merge_translations import merge


class MergeTranslationsTests(unittest.TestCase):
    def test_complete_merge_preserves_source_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            checkpoints = root / "batches"
            output = root / "output.jsonl"
            checkpoints.mkdir()
            records = [
                {"id": "a:00001", "source_text": "一", "translation": None},
                {"id": "a:00002", "source_text": "二", "translation": None},
            ]
            source.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
                encoding="utf-8",
            )
            (checkpoints / "later.json").write_text(
                json.dumps([{"id": "a:00002", "translation": "第二"}]),
                encoding="utf-8",
            )
            (checkpoints / "earlier.json").write_text(
                json.dumps([{"id": "a:00001", "translation": "第一"}]),
                encoding="utf-8",
            )

            completed, missing = merge(source, checkpoints, output)

            self.assertEqual((completed, missing), (2, 0))
            merged = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual([item["id"] for item in merged], ["a:00001", "a:00002"])
            self.assertEqual([item["translation"] for item in merged], ["第一", "第二"])

    def test_incomplete_merge_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            checkpoints = root / "batches"
            output = root / "output.jsonl"
            checkpoints.mkdir()
            source.write_text(
                json.dumps({"id": "a:00001", "translation": None}) + "\n",
                encoding="utf-8",
            )

            completed, missing = merge(source, checkpoints, output)

            self.assertEqual((completed, missing), (0, 1))
            self.assertFalse(output.exists())

    def test_conflicting_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            checkpoints = root / "batches"
            checkpoints.mkdir()
            source.write_text(
                json.dumps({"id": "a:00001", "translation": None}) + "\n",
                encoding="utf-8",
            )
            (checkpoints / "one.json").write_text(
                json.dumps([{"id": "a:00001", "translation": "甲"}]),
                encoding="utf-8",
            )
            (checkpoints / "two.json").write_text(
                json.dumps([{"id": "a:00001", "translation": "乙"}]),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "conflicting translations"):
                merge(source, checkpoints, root / "output.jsonl")


if __name__ == "__main__":
    unittest.main()
