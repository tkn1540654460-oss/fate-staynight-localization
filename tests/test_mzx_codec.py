import unittest

from tools.mzx_codec import compress_literal, decompress


class MzxCodecTests(unittest.TestCase):
    def test_roundtrip_even_and_odd_lengths(self):
        for size in (0, 1, 2, 3, 127, 128, 129, 1025):
            data = bytes((index * 37) & 0xFF for index in range(size))
            self.assertEqual(decompress(compress_literal(data)), data)


if __name__ == "__main__":
    unittest.main()

