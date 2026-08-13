import tempfile
import unittest
from pathlib import Path

from tools.extract_dialogue import decode_template, iter_records, plain_text


class ExtractDialogueTests(unittest.TestCase):
    def test_invalid_engine_pair_is_preserved(self):
        raw = b"\x81\x75\xecJ\xecF\xecH" + "問おう。".encode("cp932")
        self.assertEqual(decode_template(raw), "「{{RAW:EC4A}}{{RAW:EC46}}{{RAW:EC48}}問おう。")

    def test_plain_text_removes_controls_and_keeps_ruby_base(self):
        template = "^^　<纏,まと>う{{RAW:EC4A}}光。@n"
        self.assertEqual(plain_text(template), "纏う光。")

    def test_records_have_stable_instruction_indexes(self):
        instructions = [
            b"_PAGE(0",
            b"_ZM123(^" + "本文。".encode("cp932") + b"@n",
            b"_WTKY(",
            b"_MSA2(^" + "続き。".encode("cp932"),
            b"_TPG0(",
            b"_SELR(0," + "選択肢。".encode("cp932"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sample.ini")
            path.write_bytes(b";".join(instructions))
            records = list(iter_records(path))
        self.assertEqual([record["instruction_index"] for record in records], [1, 3, 5])
        self.assertEqual([record["command"] for record in records], ["ZM123", "MSA2", "SELR"])
        self.assertEqual(records[-1]["source_text"], "0,選択肢。")
        self.assertEqual(records[-1]["page"], 1)


if __name__ == "__main__":
    unittest.main()
