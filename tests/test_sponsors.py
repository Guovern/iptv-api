import unittest
from pathlib import Path

from utils.i18n import get_language, set_language
from utils.sponsors import HELODATA_APP_URL, HELODATA_README_URL, helodata_console_message


class SponsorPromotionTests(unittest.TestCase):
    def setUp(self):
        self.language = get_language()

    def tearDown(self):
        set_language(self.language)

    def test_sponsor_urls_use_the_requested_placements(self):
        self.assertEqual(HELODATA_README_URL, "https://helodata.com?ref=iptvapi1")
        self.assertEqual(HELODATA_APP_URL, "https://helodata.com?ref=iptvapi2")
        root = Path(__file__).resolve().parents[1]
        self.assertIn(HELODATA_README_URL, (root / "README.md").read_text(encoding="utf-8"))
        self.assertIn(HELODATA_README_URL, (root / "README_en.md").read_text(encoding="utf-8"))
        config = (root / "config" / "config.ini").read_text(encoding="utf-8")
        self.assertIn("http_proxy", config)
        self.assertIn(HELODATA_APP_URL, config)
        self.assertIn(
            "# https://helodata.com?ref=iptvapi2\n"
            "# 优惠码：iptvapi | Coupon code: iptvapi\n"
            "# HTTP 代理地址，仅用于获取订阅源和 EPG 数据；测速、媒体探测和截图保持直连 | HTTP proxy address used only to fetch subscription sources and EPG data; speed tests, media probes, and screenshots remain direct\n"
            "http_proxy =",
            config,
        )
        self.assertIn("HTTP proxy address used only to fetch subscription sources", config)
        self.assertIn("优惠码：iptvapi | Coupon code: iptvapi", config)
        self.assertIn("Helodata global proxy service covering 195+ countries", config)
        self.assertIn("了解更多请访问：", config)
        self.assertIn("Learn more at:", config)
        desktop_spec = (root / "desktop_ui" / "desktop_ui.spec").read_text(encoding="utf-8")
        self.assertIn('../docs/images/helodata.png", "docs/images', desktop_spec)

    def test_readmes_keep_helodata_promotion(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("README.md", "README_en.md"):
            content = (root / filename).read_text(encoding="utf-8")
            self.assertIn("helodata.png", content)
            self.assertIn(HELODATA_README_URL, content)

    def test_console_copy_is_localized_and_includes_the_clickable_url(self):
        set_language("zh_CN")
        chinese = helodata_console_message()
        self.assertTrue(chinese.startswith("📢 Helodata：优惠码：iptvapi"))
        self.assertIn("iptvapi，为 AI", chinese)
        self.assertIn("8000万+ IP · 195+ 国家和地区", chinese)
        self.assertIn(f"了解更多：{HELODATA_APP_URL}", chinese)
        self.assertNotIn("\n", chinese)
        self.assertNotIn("🔗", chinese)

        set_language("en")
        english = helodata_console_message()
        self.assertTrue(english.startswith("📢 Helodata: Coupon code: iptvapi"))
        self.assertIn("iptvapi, Reliable", english)
        self.assertIn("80M+ IPs · 195+ Countries", english)
        self.assertIn(f"Learn more: {HELODATA_APP_URL}", english)
        self.assertNotIn("\n", english)


if __name__ == "__main__":
    unittest.main()
