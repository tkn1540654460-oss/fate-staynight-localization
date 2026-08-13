import unittest

from tools.mzp import decode_size, encode_span


class MzpSizeTests(unittest.TestCase):
    def test_observed_small_entry(self):
        span, low = encode_span(0x528, 0x6648)
        self.assertEqual((span, low), (0x0E, 0x6648))
        self.assertEqual(decode_size(span, low), 0x6648)

    def test_exact_64k_entry(self):
        span, low = encode_span(0, 0x10000)
        self.assertEqual(decode_size(span, low), 0x10000)

    def test_large_entry_with_unaligned_start(self):
        span, low = encode_span(0x7F8, 0x23456)
        self.assertEqual(decode_size(span, low), 0x23456)


if __name__ == "__main__":
    unittest.main()

