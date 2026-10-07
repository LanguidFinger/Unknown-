import pytest

from opportunity_operator.errors import DisclosureBlocked
from opportunity_operator.policy.disclosure_filter import DisclosureFilter, squash


@pytest.mark.parametrize("text", [
    "Project Sentinel-X architecture", "project sentinel x", "PROJECT_SENTINEL_X", "Pr0ject S3ntinel X",
    "P.r.o.j.e.c.t  S.e.n.t.i.n.e.l  X", "ｐｒｏｊｅｃｔ ｓｅｎｔｉｎｅｌ ｘ", "https://x.test/?q=project%20sentinel%20x",
    "projéct sentinél x",
])
def test_variants_are_caught(text):
    f = DisclosureFilter(["Project Sentinel X"])
    assert f.hits(text)
    with pytest.raises(DisclosureBlocked):
        f.check(text, channel="t")


def test_clean_text_passes():
    DisclosureFilter(["Project Sentinel X"]).check("A grant for small business AI tools", channel="t")


def test_block_reports_hash_not_term():
    f = DisclosureFilter(["Project Sentinel X"])
    with pytest.raises(DisclosureBlocked) as e:
        f.check("see project sentinel x", channel="c")
    assert "sentinel" not in str(e.value).lower() and e.value.halt is True


def test_ignore_set_allows_terms_present_in_untrusted_input():
    f = DisclosureFilter(["Project Sentinel X"])
    present = f.hits("a page that mentions project sentinel x")
    f.check("echo project sentinel x", channel="out", ignore=present)


def test_too_short_term_refused():
    with pytest.raises(ValueError):
        DisclosureFilter(["AI"])


def test_squash_is_idempotent():
    assert squash(squash("H3 arth-X")) == squash("H3 arth-X")
