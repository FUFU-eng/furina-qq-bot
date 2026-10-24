class PixivError(RuntimeError):
    pass

class PixivNetworkError(PixivError):
    """API/network temporarily unavailable; not proof of QQ delivery failure."""

class PixivAccessError(PixivError):
    """Access denied/rate limited; do not bypass or blindly retry."""

class PixivDownloadError(PixivNetworkError):
    """One original exhausted its bounded download budget; other works may proceed."""
