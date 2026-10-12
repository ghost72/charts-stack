"""
Shared error types for charts-recommend.
"""


class FetchError(Exception):
    """
    Transport error when fetching a recommendation source (timeout, HTTP error,
    connection error) - AFTER all retries have failed.

    IMPORTANT: This is deliberately separate from an *empty but successful*
    result (e.g. ListenBrainz has not generated any playlists yet). An empty
    result is NOT an error but missing data; only a FetchError signals a real
    failure (-> status="error" + Pushover alarm).
    """
