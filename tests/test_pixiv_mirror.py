"""节点连不上 i.pximg.net 时走 pixiv.re 镜像；当前标签页被关掉时切回还活着的标签页。"""
from tests import _env  # noqa: F401  (must be first)

import unittest
from unittest import mock

from furina.plugins.image_search import browser, download


class PixivMirrorTest(unittest.TestCase):
    def test_mirror_urls(self):
        self.assertEqual(download.pixiv_mirror_urls("https://i.pximg.net/img-original/img/2019/09/01/05/14/38/76547765_p0.png"),
                         ["https://pixiv.re/76547765.png", "https://pixiv.re/76547765-1.png"])
        self.assertEqual(download.pixiv_mirror_urls("https://i.pximg.net/img-original/img/2019/09/01/05/14/38/76547765_p2.jpg"),
                         ["https://pixiv.re/76547765-3.jpg"])
        self.assertEqual(download.pixiv_mirror_urls("https://example.com/a.png"), [])

    def test_pximg_falls_back_to_mirror(self):
        with mock.patch.object(download, "_try_download_plain", return_value=None) as plain, \
                mock.patch.object(download, "_download_pixiv_mirror", return_value=b"IMG") as mirror:
            self.assertEqual(download._try_download_bytes("https://i.pximg.net/img-original/img/x/1_p0.png", "pixiv"), b"IMG")
        plain.assert_called_once()
        mirror.assert_called_once()
        with mock.patch.object(download, "_try_download_plain", return_value=b"OK"), \
                mock.patch.object(download, "_download_pixiv_mirror") as mirror:
            self.assertEqual(download._try_download_bytes("https://i.pximg.net/img-original/img/x/1_p0.png", "pixiv"), b"OK")
        mirror.assert_not_called()

    def test_ensure_live_window(self):
        class D:
            window_handles = ["A", "B"]
            switched = None

            @property
            def current_window_handle(self):
                raise Exception("no such window: target window already closed")

            class switch_to:  # noqa: N801
                pass

        d = D()
        d.switch_to = mock.Mock()
        with mock.patch.object(browser, "minimized_navigate", side_effect=lambda drv, act: act()):
            browser.ensure_live_window(d)
        d.switch_to.window.assert_called_once_with("A")


if __name__ == "__main__":
    unittest.main()
