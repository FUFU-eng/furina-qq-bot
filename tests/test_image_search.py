import unittest
from unittest.mock import patch

from tests import _env  # noqa: F401,E402

from furina.plugins.image_search.search import (
    _extract_google_result_pages,
    _find_google_exact_match_url,
    _is_trusted_exact_page,
    _prioritize_exact_pages,
)
from furina.plugins.image_search import browser
from furina.plugins.image_search.sites import _extract_tweet_status_image


class FakeElement:
    def __init__(self, href="", text="", attrs=None):
        self._href = href
        self.text = text
        self._attrs = attrs or {}

    def get_attribute(self, name):
        if name == "href":
            return self._href
        return self._attrs.get(name, "")

    def find_elements(self, *_args):
        return []


class FakeDriver:
    def __init__(self, current_url, links, page_source=""):
        self.current_url = current_url
        self._links = links
        self.page_source = page_source

    def find_elements(self, by=None, selector=None):
        if selector and (selector.startswith("img[") or selector.startswith("meta[")):
            return []
        return self._links


class FakeUser32:
    def __init__(self, foreground=999, iconic=False):
        self.foreground = foreground
        self.iconic = iconic
        self.show_calls = []

    def IsIconic(self, _hwnd):
        return self.iconic

    def GetForegroundWindow(self):
        return self.foreground

    def ShowWindow(self, hwnd, command):
        self.show_calls.append((hwnd, command))
        self.iconic = True
        return 1


class ExactMatchTests(unittest.TestCase):
    def test_finds_exact_match_link_and_resolves_relative_url(self):
        driver = FakeDriver(
            "https://www.google.com/search?lens=1",
            [
                FakeElement(
                    "/search?udm=48&lens=1",
                    "完全匹配",
                    {"jsname": "pxBnId"},
                )
            ],
        )

        self.assertEqual(
            "https://www.google.com/search?udm=48&lens=1",
            _find_google_exact_match_url(driver),
        )

    def test_extracts_exact_x_status_page(self):
        driver = FakeDriver(
            "https://www.google.com/search?udm=48&lens=1",
            [
                FakeElement(
                    "https://x.com/HgHgure/status/2090712539521052850",
                    "Remielle artwork on X",
                )
            ],
        )

        pages = _extract_google_result_pages(driver)

        self.assertEqual(
            "https://x.com/HgHgure/status/2090712539521052850",
            pages[0]["link"],
        )

    def test_exact_detail_pages_are_before_exact_profiles(self):
        pages = _prioritize_exact_pages(
            [
                {"link": "https://x.com/HgHgure", "exact": True},
                {"link": "https://x.com/HgHgure/status/2090712539521052850", "exact": True},
            ]
        )

        self.assertIn("/status/", pages[0]["link"])
    def test_exact_pages_are_before_normal_pages(self):
        pages = _prioritize_exact_pages(
            [
                {"link": "https://x.com/HgHgure", "exact": False},
                {"link": "https://x.com/HgHgure/status/2090712539521052850", "exact": True},
            ]
        )

        self.assertTrue(pages[0]["exact"])
        self.assertIn("/status/", pages[0]["link"])

    def test_only_exact_detail_pages_get_trusted_original_threshold(self):
        self.assertTrue(_is_trusted_exact_page("https://x.com/HgHgure/status/2090712539521052850"))
        self.assertFalse(_is_trusted_exact_page("https://x.com/HgHgure"))


    def test_extracts_tweet_image_from_syndication_when_dom_has_no_media(self):
        driver = FakeDriver(
            "https://x.com/HgHgure/status/2090712539521052850",
            [],
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "mediaDetails": [
                        {
                            "type": "photo",
                            "media_url_https": "https://pbs.twimg.com/media/HQOznAWaIAADdcy.jpg",
                        }
                    ]
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                self.url = url
                self.params = kwargs.get("params")
                return FakeResponse()

        with patch("httpx.Client", FakeClient):
            with patch("furina.plugins.image_search.sites.WebDriverWait") as wait_cls:
                wait_cls.return_value.until.return_value = None
                self.assertEqual(
                    ["https://pbs.twimg.com/media/HQOznAWaIAADdcy.jpg?format=jpg&name=orig"],
                    _extract_tweet_status_image(driver, 0),
                )

    def test_suppression_thread_minimizes_automatic_window_restore(self):
        fake_user32 = FakeUser32(foreground=999, iconic=False)
        with patch.object(browser, "_user32", fake_user32), \
                patch.object(browser, "_valid_browser_hwnd", return_value=123), \
                patch.object(browser, "aborted", return_value=False), \
                patch.object(browser, "_user_open_until", 0.0):
            browser._start_suppression(1)
            browser._suppression_stop.wait(0.35)
            browser._stop_suppression()

        self.assertTrue(fake_user32.show_calls)
        self.assertEqual(123, fake_user32.show_calls[0][0])

    def test_automatic_foreground_restore_is_not_user_open(self):
        with patch.object(browser._user32, "GetForegroundWindow", return_value=123), \
                patch.object(browser, "_last_input_age_seconds", return_value=0.2), \
                patch.object(browser, "_is_cursor_on_taskbar", return_value=False):
            self.assertFalse(browser._is_user_open_request(123))

    def test_recent_taskbar_click_is_user_open(self):
        with patch.object(browser._user32, "GetForegroundWindow", return_value=123), \
                patch.object(browser, "_last_input_age_seconds", return_value=0.2), \
                patch.object(browser, "_is_cursor_on_taskbar", return_value=True):
            self.assertTrue(browser._is_user_open_request(123))



    def test_danbooru_rest_api_uses_compliant_user_agent(self):
        from furina.plugins.image_search.sites import _fetch_danbooru_post_api

        captured = {}

        class FakeResp:
            status_code = 200
            def json(self):
                return {"id": 9311200, "file_url": "https://cdn.donmai.us/original/20/93/2093a67fd.png"}

        class FakeClient:
            def __init__(self, *args, **kwargs):
                captured["headers"] = kwargs.get("headers") or {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def get(self, url, **kwargs):
                captured["url"] = url
                return FakeResp()

        with patch("httpx.Client", FakeClient):
            post = _fetch_danbooru_post_api("9311200")
        self.assertEqual("https://cdn.donmai.us/original/20/93/2093a67fd.png", post.get("file_url"))
        self.assertIn("FurinaBot/2.0", captured["headers"].get("User-Agent", ""))

    def test_stealth_init_js_present(self):
        self.assertIn("RTCPeerConnection", browser._STEALTH_INIT_JS)
        self.assertIn("webdriver", browser._STEALTH_INIT_JS)


if __name__ == "__main__":
    unittest.main()


