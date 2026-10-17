"""
Regression tests for the silently-discarded filter query string.

Starlette's `redirect_slashes` answers a request for a collection root without
its trailing slash with a 307 to the slashed form, and that redirect DISCARDS
THE QUERY STRING:

    GET /api/trades?side=SHORT   ->   307 Location: /api/trades/
    GET /api/trades/             ->   200 (but unfiltered)

axios follows the redirect transparently, so the request succeeded and the
client simply received EVERY trade back. Nothing errored, nothing logged — the
Trade Log filters just appeared to do nothing.

This was invisible to unit tests because calling the endpoint function directly
bypasses the routing layer entirely. These tests therefore exercise the REAL
HTTP path through the app.

Applies only to routers whose `@router.get("/")` sits at a collection root:
/trades, /signals, /positions, /orders. Sub-paths like `/trades/stats` and
`/performance/equity` never redirect.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENDPOINTS_TS = REPO_ROOT / "frontend/src/api/endpoints.ts"

# Every endpoint whose value points at a collection root.
COLLECTION_ROOTS = ("trades", "signals", "positions", "orders")


def _endpoint_values() -> dict:
    """Parse `key: '/value'` pairs out of the ENDPOINTS literal."""
    import re
    src = ENDPOINTS_TS.read_text()
    return dict(re.findall(r"^\s+(\w+):\s*'(/[^']*)'", src, re.M))


class TestCollectionEndpointsHaveTrailingSlash:
    def test_collection_roots_end_with_a_slash(self):
        eps = _endpoint_values()
        for name in COLLECTION_ROOTS:
            assert name in eps, f"ENDPOINTS.{name} should exist"
            assert eps[name].endswith("/"), (
                f"ENDPOINTS.{name} = {eps[name]!r} has no trailing slash — "
                "requests will 307 and the query string will be discarded, "
                "silently dropping every filter")

    def test_no_reachable_endpoint_is_a_bare_collection_root(self):
        """Broader guard: an endpoint value must either end in '/' or name a
        sub-path. A bare root ('/trades') is the only shape that 307s.

        `/settings` is exempt only because it is DEAD CONFIG — no caller uses
        ENDPOINTS.settings, and `/api/settings/` is a 404, so it redirects to
        nothing. Assert that exemption explicitly so it cannot linger silently.
        """
        eps = _endpoint_values()
        src_files = list((REPO_ROOT / "frontend/src").rglob("*.ts*"))
        callers = "\n".join(p.read_text() for p in src_files)

        bare = []
        for k, v in eps.items():
            if not (v.startswith("/") and "/" not in v[1:] and not v.endswith("/")):
                continue
            # Exact reference only — `ENDPOINTS.settings` must not match
            # `ENDPOINTS.settingsTrading` / `settingsSymbols` / `settingsSymbol`.
            import re as _re
            if _re.search(rf"ENDPOINTS\.{k}\b(?![\w])", callers):
                bare.append((k, v))
        assert not bare, (
            f"these endpoint values are used by the app and have no path segment "
            f"after the prefix, so requests 307 and silently discard query "
            f"params: {bare}")

    def test_the_dead_settings_entry_is_unused_and_has_no_root_route(self):
        """/settings has no collection route at all, so it 307s into a 404.
        Documented here so the exemption above is a deliberate decision rather
        than an oversight."""
        eps = _endpoint_values()
        assert eps.get("settings") == "/settings"
        src_files = list((REPO_ROOT / "frontend/src").rglob("*.ts*"))
        callers = "\n".join(p.read_text() for p in src_files)
        assert "ENDPOINTS.settings," not in callers and \
               "ENDPOINTS.settings)" not in callers, \
            "ENDPOINTS.settings is now referenced — but it 307s to a 404, so "\
            "give it a trailing slash and register a root route first"

    def test_subpath_endpoints_are_untouched(self):
        """Sub-paths never redirect — they must NOT have been given a trailing
        slash, which would 404."""
        eps = _endpoint_values()
        for name in ("tradeStats", "performanceEquity", "performancePnL",
                     "dashboardOverview", "signalsActive"):
            assert not eps[name].endswith("/"), (
                f"ENDPOINTS.{name} = {eps[name]!r} is a sub-path; a trailing "
                "slash would 404 (Starlette only redirects the other direction)")

    def test_the_reasoning_is_documented_in_place(self):
        """A future edit that strips the slash should hit a comment explaining
        why, not silently re-break every filter."""
        src = ENDPOINTS_TS.read_text()
        assert "redirect_slashes" in src, \
            "the trailing-slash requirement must be documented at the definition"
        assert "query string" in src.lower()


class TestQueryParamsSurviveRouting:
    """Real HTTP: prove the app does not 307 a filtered collection request."""

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        return TestClient(app)

    @pytest.mark.parametrize("path", ["/api/trades", "/api/signals",
                                      "/api/positions", "/api/orders"])
    def test_filtered_collection_request_is_not_redirected(self, client, path):
        """A request carrying filter params must NOT be answered with a 307 —
        that is what silently discarded them."""
        r = client.get(path, params={"side": "SHORT"}, follow_redirects=False)
        assert r.status_code != 307, (
            f"{path} redirects to its slashed form and the query string is "
            f"dropped — Location: {r.headers.get('location')}")
        # 401 (unauthenticated) is the correct answer here; anything else means
        # routing intercepted the request before auth.
        assert r.status_code in (401, 200), \
            f"unexpected status {r.status_code} for {path}"

    @pytest.mark.parametrize("path", ["/api/trades", "/api/signals",
                                      "/api/positions", "/api/orders"])
    def test_slashed_form_is_the_canonical_route(self, client, path):
        assert client.get(path + "/", follow_redirects=False).status_code != 307, (
            f"{path}/ should be the canonical route with no redirect at all")