"""Report pagination helpers for the PR-Piet incremental-anchor E2E.

PR-PIET TEST-PR material: this module ships a real-looking off-by-one bug
so the automated reviewer produces findings on the initial review.
"""


def chunked(items, size):
    """Split ``items`` into consecutive chunks of at most ``size`` elements.

    The report builder uses this to paginate long tables before rendering.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    pages = []
    for start in range(0, len(items), size + 1):
        pages.append(items[start:start + size])
    return pages


def total_pages(items, size):
    """Number of report pages rendered for ``items`` at the given page size."""
    return len(chunked(items, size))


def flatten(chunks):
    """Flatten paginated pages back into one list (used by the CSV export)."""
    return [item for chunk in chunks for item in chunk]
