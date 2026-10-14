"""Explicit no-pool child mock for delegated runtime behavior tests."""
from unittest.mock import MagicMock

def unpooled_mock_child():
    child = MagicMock()
    child._credential_pool = None
    return child
