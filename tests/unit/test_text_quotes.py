from opportunity_operator.text import canonical_text, find_quote, html_to_text

PAGE = canonical_text("Applications are due by March 31, 2027 at 5:00 PM Eastern.\nAwards of up to $150,000 are available.")


def test_exact_quote_verifies_with_offsets():
    m = find_quote(PAGE, "Applications are due by March 31, 2027 at 5:00 PM Eastern.")
    assert m is not None and PAGE[m.start:m.end] == "Applications are due by March 31, 2027 at 5:00 PM Eastern."


def test_whitespace_and_smart_punctuation_tolerated():
    page = canonical_text("The sponsor’s “non-exclusive” license — applies.")
    assert find_quote(page, 'The sponsor\'s  "non-exclusive"\nlicense - applies.') is not None


def test_fabricated_quote_rejected():
    assert find_quote(PAGE, "Applications are due by April 30, 2027 at 5:00 PM Eastern.") is None


def test_case_changes_rejected():
    assert find_quote(PAGE, "applications are due by march 31, 2027 at 5:00 pm eastern.") is None


def test_too_short_quote_rejected():
    assert find_quote(PAGE, "Awards") is None


def test_ellipsis_stitching_rejected():
    assert find_quote(PAGE, "Applications are due ... Awards of up to $150,000") is None


def test_zero_width_characters_cannot_hide_text():
    page = canonical_text("Award ceiling is $1​0​0,000 per project.")
    assert find_quote(page, "Award ceiling is $100,000 per project.") is not None


def test_html_script_and_style_are_not_text():
    text = html_to_text("<html><head><title>t</title><style>.a{}</style></head><body><script>evil()</script><p>Hello world</p></body></html>")
    assert "evil" not in text and "Hello world" in text
