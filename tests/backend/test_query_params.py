"""
Regression tests for the silently-ignored query params.

THE BUG: `api.get(url, params)` wraps its second argument as
`apiClient.get(url, { params })` — it expects the query params DIRECTLY. But
seven call sites passed a full axios config instead:

    api.get(ENDPOINTS.trades, { params })        // WRONG
    api.get(ENDPOINTS.trades, params)           // RIGHT

which serialised to `?params[side]=SHORT&params[page]=1`. The backend looks for
`side`, finds nothing, and returns every trade with a 200. Nothing errored,
nothing was logged — the Trade Log's Side / Outcome / Symbol filters, the
Scanner's filters and the Performance range buttons all silently did nothing.

This was caught by reading the ACCESS LOG, which showed the smoking gun:

    GET /api/trades/?params%5Bpage%5D=1&params%5Bside%5D=SHORT

Verification notes: every earlier attempt checked the backend endpoint function
in-process, which bypasses the frontend entirely. The defect only exists in what
the BROWSER actually puts on the wire, so these tests assert on the source that
builds the request.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend/src"

PAGE_FILES = sorted(
    p for p in SRC.rglob("*.ts*")
    if "api.get" in p.read_text() or "ENDPOINTS" in p.read_text()
)


def _all_source() -> str:
    return "\n".join(p.read_text() for p in PAGE_FILES)


class TestNoDoubleWrappedQueryParams:
    def test_no_caller_passes_an_axios_config_to_api_get(self):
        offenders = []
        for p in PAGE_FILES:
            for m in re.finditer(r"api\.get<[^>]*>\([^)]*\)", p.read_text()):
                call = m.group(0)
                if re.search(r",\s*\{\s*params\b", call):
                    offenders.append((p.relative_to(REPO_ROOT).as_posix(), call.strip()))
        assert not offenders, (
            "these calls pass an axios config to a helper that expects query "
            "params, so axios sends params[key]=value and the backend ignores "
            f"every filter:\n  " + "\n  ".join(f"{f}: {c}" for f, c in offenders))

    @pytest.mark.parametrize("name", ["trades", "signals", "tradeStats",
                                      "performanceEquity", "performancePnL"])
    def test_known_filter_call_sites_pass_params_directly(self, name):
        src = _all_source()
        hits = [m.group(0) for m in
                re.finditer(rf"api\.get<[^>]*>\([^)]*{name}[^)]*\)", src)]
        assert hits, f"expected at least one api.get call using {name}"
        for call in hits:
            assert not re.search(r",\s*\{\s*params\s*[:}]", call), (
                f"{call} double-wraps its query params")


class TestAxiosHelperContract:
    def test_helper_wraps_params_exactly_once(self):
        client = (SRC / "api/client.ts").read_text()
        # The get helper must feed its argument to axios as `params`.
        assert re.search(r"apiClient\.get<T>\(\s*url,\s*\{\s*params:", client), \
            "api.get must pass its argument to axios as the params config"

    def test_helper_rejects_a_nested_params_wrapper(self):
        """A single `{ params: ... }` key is the tell-tale mistake; the helper
        must detect it rather than serialise it into params[params]."""
        client = (SRC / "api/client.ts").read_text()
        assert "queryParams" in client, \
            "the helper must guard against the nested-params mistake"
        assert "'params' in params" in client or '"params" in params' in client, \
            "the guard must detect a lone `params` key"

    def test_the_reasoning_is_documented_in_place(self):
        client = (SRC / "api/client.ts").read_text()
        assert "params[side]" in client or "params[key]" in client, \
            "the exact symptom must be documented so a future edit recognises it"


class TestEndpointsThatCarryFilters:
    """Sanity: the endpoints the bug affected are still slash-terminated, since
    an unslashed collection root ALSO discards query params (via a 307). Both
    defects stacked here and either alone would have broken the filters."""

    def test_collection_roots_keep_their_trailing_slash(self):
        from tests.backend.test_endpoint_slashes import _endpoint_values, COLLECTION_ROOTS
        eps = _endpoint_values()
        for name in COLLECTION_ROOTS:
            assert eps[name].endswith("/"), (
                f"ENDPOINTS.{name} = {eps[name]!r} would 307 and drop the "
                "query string — a second way for the filters to vanish")