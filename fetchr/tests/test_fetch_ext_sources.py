from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import requests

import run.fetch_ext_sources as ext


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | None = None,
        text: str = "",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self._payload = payload or {}
        self.text = text
        self._chunks = list(chunks or [])

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload

    def iter_content(self, chunk_size: int = 1024):  # noqa: ARG002
        for chunk in self._chunks:
            yield chunk

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        self.close()
        return False


def test_treasury_metrics_cache_reuses_computation(monkeypatch, tmp_path: Path) -> None:
    ext._TREASURY_METRICS_CACHE.clear()
    calls = {"load": 0, "compute": 0}

    def _fake_load(spec, cfg, diagnostics=None):  # noqa: ANN001
        _ = diagnostics
        calls["load"] += 1
        return pd.DataFrame(
            {
                "record_date": pd.to_datetime(["2024-01-31"]),
                "outstanding_amount": [100.0],
                "security_type": ["Note"],
                "remaining_years": [2.0],
                "issue_date": pd.to_datetime(["2023-01-31"]),
                "original_term_years": [3.0],
                "coupon_rate": [3.5],
                "yield": [3.6],
            }
        )

    def _fake_compute(ledger):  # noqa: ANN001
        calls["compute"] += 1
        return pd.DataFrame(
            {
                "record_date": pd.to_datetime(["2024-01-31"]),
                "wam_tot": [2.0],
                "bill_ratio": [0.25],
            }
        )

    monkeypatch.setattr(ext, "_load_treasury_ledger", _fake_load)
    monkeypatch.setattr(ext, "_compute_treasury_metrics", _fake_compute)

    cfg = {"CONFIG_DIR": tmp_path}
    base_spec = {
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "start_date": "2020-01-01",
        "end_date": "2025-12-31",
        "value_key": "wam_tot",
        "use_metrics_cache": True,
    }
    s1 = ext.fetch_treasury_mspd(base_spec, cfg)
    s2 = ext.fetch_treasury_mspd(
        {
            **base_spec,
            "name": "treasury_bill_ratio",
            "value_key": "bill_ratio",
        },
        cfg,
    )

    assert calls["load"] == 1
    assert calls["compute"] == 1
    assert float(s1.iloc[0]) == pytest.approx(2.0)
    assert float(s2.iloc[0]) == pytest.approx(0.25)


def test_treasury_api_raises_when_max_pages_hit_without_partial(monkeypatch, tmp_path: Path) -> None:
    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        assert method == "GET"
        _ = url
        page = int(kwargs["params"]["page[number]"])
        payload = {
            "data": [
                {
                    "record_date": "2024-01-31",
                    "maturity_date": "2025-01-31",
                    "outstanding_amount": "100",
                }
            ],
            "meta": {"total-pages": "3", "page-number": str(page)},
        }
        return _FakeResponse(status_code=200, payload=payload)

    monkeypatch.setattr(ext.requests, "request", _fake_request)
    spec = {
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "value_key": "wam_tot",
        "max_pages": 1,
        "allow_partial_results": False,
    }
    cfg = {"CONFIG_DIR": tmp_path, "HTTP_TIMEOUT_SECONDS": 2}

    with pytest.raises(RuntimeError, match="max_pages"):
        ext._fetch_treasury_mspd_api(spec, cfg)


def test_download_binary_with_cap_raises_when_limit_exceeded(monkeypatch) -> None:
    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        assert method == "GET"
        _ = url
        _ = kwargs
        return _FakeResponse(status_code=200, chunks=[b"a" * 10, b"b" * 10])

    monkeypatch.setattr(ext.requests, "request", _fake_request)
    with pytest.raises(RuntimeError, match="max_bytes"):
        ext._download_binary_with_cap(
            "https://example.com/file.zip",
            timeout=5,
            user_agent="fetchr-test",
            retries=0,
            retry_backoff_seconds=0.0,
            max_bytes=15,
        )


def test_usda_snap_rejects_oversized_cached_zip(tmp_path: Path) -> None:
    cache_zip = tmp_path / "snap_cached.zip"
    cache_zip.write_bytes(b"not-a-real-zip")
    spec = {
        "name": "snap_persons",
        "source": "usda_snap",
        "cache_zip_path": str(cache_zip),
        "use_cached_zip": True,
        "force_download": False,
        "max_zip_bytes": 4,
    }
    cfg = {"CONFIG_DIR": tmp_path}

    with pytest.raises(RuntimeError, match="cache_zip_path exceeds max_zip_bytes"):
        ext.fetch_usda_snap(spec, cfg)


def test_request_with_retry_counts_retries_without_double_count(monkeypatch) -> None:
    attempts = {"n": 0}

    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        _ = (method, url, kwargs)
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeResponse(status_code=500)
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(ext.requests, "request", _fake_request)
    diagnostics: dict = {}
    resp = ext._request_with_retry(
        "GET",
        "https://example.com/retry",
        timeout=5,
        user_agent="fetchr-test",
        retries=2,
        retry_backoff_seconds=0.0,
        diagnostics=diagnostics,
    )
    assert resp.status_code == 200
    assert diagnostics["http_attempts_total"] == 2
    assert diagnostics["http_retries_used"] == 1
    assert diagnostics["http_status_codes"] == [500, 200]


def test_fetch_treasury_mspd_api_records_and_pages_accounted_for_partial_max_records(monkeypatch) -> None:
    def _fake_request(method, url, **kwargs):  # noqa: ANN001
        _ = method
        _ = url
        page = int(kwargs["params"]["page[number]"])
        if page == 1:
            payload = [{"id": 1}, {"id": 2}, {"id": 3}]
        elif page == 2:
            payload = [{"id": 4}, {"id": 5}, {"id": 6}]
        else:
            payload = []
        return _FakeResponse(status_code=200, payload={"data": payload, "meta": {"total-pages": "5", "page-number": str(page)}})

    monkeypatch.setattr(ext.requests, "request", _fake_request)
    diagnostics: dict = {}
    df = ext._fetch_treasury_mspd_api(
        {"name": "treasury_wam_tot", "source": "treasury_mspd", "max_records": 5, "allow_partial_results": True},
        {"CONFIG_DIR": Path("/tmp")},
        diagnostics=diagnostics,
    )
    assert len(df) == 5
    assert diagnostics["pages_fetched"] == 2
    assert diagnostics["records_fetched"] == 5
    assert diagnostics["partial_results"] is True


def test_treasury_mspd_api_mode_attaches_diagnostics(monkeypatch, tmp_path: Path) -> None:
    def _fake_api(spec, cfg, diagnostics=None):  # noqa: ANN001
        _ = (spec, cfg, diagnostics)
        return pd.DataFrame(
            {
                "record_date": ["2024-01-31", "2024-02-29"],
                "maturity_date": ["2025-01-31", "2025-02-28"],
                "outstanding_amount": [100.0, 200.0],
                "security_type": ["Bill", "Note"],
                "issue_date": ["2024-01-15", "2024-02-15"],
                "coupon_rate": [0.0, 2.5],
                "yield": [0.0, 2.2],
            }
        )

    def _fake_compute(ledger):  # noqa: ANN001
        _ = ledger
        return pd.DataFrame(
            {
                "record_date": pd.to_datetime(["2024-02-29"]),
                "wam_tot": [2.5],
            }
        )

    monkeypatch.setattr(ext, "_fetch_treasury_mspd_api", _fake_api)
    monkeypatch.setattr(ext, "_compute_treasury_metrics", _fake_compute)
    ext._TREASURY_METRICS_CACHE.clear()

    cfg = {"CONFIG_DIR": tmp_path}
    spec = {
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "value_key": "wam_tot",
        "use_metrics_cache": False,
    }
    out = ext.fetch_treasury_mspd(spec, cfg)
    diag = out.attrs.get("fetch_diagnostics")
    assert isinstance(diag, dict)
    assert diag.get("adapter") == "treasury_mspd"
    assert diag.get("mode") == "api"
    assert int(diag.get("rows_input", 0)) == 2
    assert int(diag.get("rows_output", 0)) == len(out)


def test_usda_snap_input_mode_attaches_diagnostics(tmp_path: Path) -> None:
    src = tmp_path / "snap_input.csv"
    pd.DataFrame(
        {
            "fiscal_year_month": ["Jan 2024", "Feb 2024", "Mar 2024"],
            "fiscal_year": [2024, 2024, 2024],
            "persons_thousands": [100.0, 105.0, 110.0],
        }
    ).to_csv(src, index=False)
    spec = {
        "name": "snap_persons",
        "source": "usda_snap",
        "input_path": str(src),
        "value_key": "persons_thousands",
    }
    cfg = {"CONFIG_DIR": tmp_path}

    out = ext.fetch_usda_snap(spec, cfg)
    diag = out.attrs.get("fetch_diagnostics")
    assert isinstance(diag, dict)
    assert diag.get("adapter") == "usda_snap"
    assert diag.get("mode") == "input_source"
    assert int(diag.get("rows_output", 0)) == len(out)


def test_treasury_mspd_input_mode_attaches_diagnostics(tmp_path: Path) -> None:
    src = tmp_path / "treasury_input.csv"
    pd.DataFrame(
        {
            "record_date": ["2024-01-31", "2024-01-31"],
            "maturity_date": ["2025-01-31", "2030-01-31"],
            "issue_date": ["2023-01-31", "2020-01-31"],
            "outstanding_amount": [100.0, 200.0],
            "security_type": ["Bill", "Note"],
            "coupon_rate": [0.0, 3.5],
            "yield": [0.0, 3.7],
        }
    ).to_csv(src, index=False)
    spec = {
        "name": "treasury_wam_tot",
        "source": "treasury_mspd",
        "input_path": str(src),
        "value_key": "wam_tot",
        "use_metrics_cache": False,
    }
    cfg = {"CONFIG_DIR": tmp_path}

    out = ext.fetch_treasury_mspd(spec, cfg)
    diag = out.attrs.get("fetch_diagnostics")
    assert isinstance(diag, dict)
    assert diag.get("adapter") == "treasury_mspd"
    assert diag.get("mode") == "input_source"
    assert int(diag.get("rows_input", 0)) == 2
    assert int(diag.get("ledger_rows", 0)) >= 1
    assert int(diag.get("rows_output", 0)) == len(out)
