"""Stub re-exporting BrowserProvider from plugins for legacy compatibility."""
from plugins.browser.browser_use.provider import BrowserProvider

class CloudBrowserProvider(BrowserProvider):
    """Legacy alias for BrowserProvider."""
    pass
