"""
What this client puts on the wire, independent of what is installed beside it.

httpx composes `Accept-Encoding` from the decoders it can import, so a library
added for an unrelated reason silently changes how this client talks to
OpenSearch. That is how the LangGraph backend ended up reporting OpenSearch and
the registry as unreachable while a plain urllib request from the same container
answered instantly: `langgraph` -> `langsmith` -> `zstandard`, httpx then
advertised `zstd`, and OpenSearch 2.19 accepted it and never sent a readable
body.
"""
from __future__ import annotations

from app.sources.opensearch import OpenSearchClient


def test_the_client_names_the_encodings_it_accepts():
    client = OpenSearchClient(base_url="http://opensearch:9200")
    try:
        accept = client._client.headers.get("accept-encoding", "")
        assert accept == "gzip, deflate", (
            "Accept-Encoding must be pinned here, not inherited from whatever "
            "decoders the image happens to have installed"
        )
        # The specific one that hangs OpenSearch. Asserted by name because the
        # failure it causes looks like an outage, not like a bad header.
        assert "zstd" not in accept
        assert "br" not in accept
    finally:
        pass
