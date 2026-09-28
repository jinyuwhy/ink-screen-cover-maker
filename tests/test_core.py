import unittest

from PIL import Image

import eink_cover_maker as app


class CoreTests(unittest.TestCase):
    def test_normalize_and_relevance(self):
        self.assertEqual(app.normalize_title(" 看不见的中东： "), "看不见的中东")
        exact = app.title_relevance("三体", "三体")
        unrelated = app.title_relevance("三体", "时间简史")
        self.assertGreater(exact, unrelated)

    def test_custom_resolution_output(self):
        source = Image.new("RGB", (600, 900), "gray")
        result = app.make_wallpaper(
            source,
            "完整封面（推荐）",
            "256级灰度（推荐）",
            1.15,
            True,
            (824, 1648),
        )
        self.assertEqual(result.size, (824, 1648))
        self.assertEqual(result.mode, "L")

    def test_safe_filename(self):
        self.assertEqual(app.safe_filename('A:B/C?'), "A_B_C_")


if __name__ == "__main__":
    unittest.main()
