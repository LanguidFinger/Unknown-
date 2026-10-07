from __future__ import annotations

from pathlib import Path

import pytest

from opportunity_operator.adapters.fake_fetcher import FakeFetcher
from opportunity_operator.adapters.mock_llm import MockLLM
from opportunity_operator.adapters.mock_search import MockSearch
from opportunity_operator.app import App, build_app, init_data_dir
from opportunity_operator.config import Settings
from opportunity_operator.owner import OwnerSession
from opportunity_operator.safe_fs import DataDir
from opportunity_operator.store.db import connect

PAGE_URL = "https://programs.example.gov/ai-grant"
PAGE_HTML = (
    b"<html><head><title>x</title><script>var secret=1;</script></head><body>"
    b"<h1>Synthetic AI Innovation Grant</h1>"
    b"<p>Applications are due by March 31, 2027 at 5:00 PM Eastern.</p>"
    b"<p>Awards of up to $150,000 are available to small businesses.</p>"
    b"<p>The sponsor receives a non-exclusive license to all deliverables.</p>"
    b"</body></html>"
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def datadir(settings: Settings) -> DataDir:
    return init_data_dir(settings)


@pytest.fixture
def owner(settings: Settings, datadir: DataDir):
    o = OwnerSession(datadir, settings, confirm=lambda _p: True)
    yield o
    o.close()


@pytest.fixture
def agent_conn(datadir: DataDir):
    c = connect(datadir.db_path(), "agent")
    yield c
    c.close()


def default_responder(prompt, schema):
    if schema.__name__ == "ExtractionOut":
        return {"items": [
            {"field": "deadline", "value": "2027-03-31", "quote": "Applications are due by March 31, 2027 at 5:00 PM Eastern."},
            {"field": "award_max", "value": "150000", "quote": "Awards of up to $150,000 are available to small businesses."},
            {"field": "ip.license", "value": "non-exclusive license to sponsor", "quote": "The sponsor receives a non-exclusive license to all deliverables."},
        ]}
    return {"fit": 6, "rationale": "synthetic rationale"}


@pytest.fixture
def make_app(settings: Settings):
    apps: list[App] = []

    def _make(responder=default_responder, pages=None) -> tuple[App, MockLLM, FakeFetcher]:
        llm = MockLLM(responder)
        fetcher = FakeFetcher(pages if pages is not None else {PAGE_URL: ("text/html", PAGE_HTML)})
        a = build_app(settings, llm=llm, search=MockSearch(), fetcher=fetcher,
                      price_in_per_mtok=1.0, price_out_per_mtok=5.0)
        apps.append(a)
        return a, llm, fetcher

    yield _make
    for a in apps:
        a.close()
