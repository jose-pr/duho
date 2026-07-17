"""Tests for duho.text (expand, pysafe, snakecase, camelcase)."""

from duho.text import camelcase, expand, pysafe, snakecase


class TestExpand:
    def test_digit_range_not_zero_padded(self):
        # Verified 2026-07-17 vs coquilib: output is NOT zero-padded.
        assert list(expand("h[01-03]")) == ["h1", "h2", "h3"]

    def test_letter_range(self):
        assert list(expand("p[A-C]")) == ["pA", "pB", "pC"]

    def test_no_bracket_passthrough(self):
        assert list(expand("plain")) == ["plain"]

    def test_nested_two_ranges_cartesian(self):
        # Order is an implementation detail; assert set-equality.
        assert set(expand("x[1-2]y[1-2]")) == {"x1y1", "x2y1", "x1y2", "x2y2"}

    def test_nested_two_ranges_count(self):
        assert len(list(expand("x[1-2]y[1-2]"))) == 4


class TestPysafe:
    def test_keyword_gets_trailing_underscore(self):
        assert pysafe("class") == "class_"

    def test_symbol_prefix(self):
        assert pysafe("+x") == "plus_x"

    def test_hyphen_and_space_become_underscore(self):
        assert pysafe("a-b c") == "a_b_c"

    def test_bare_symbol_maps_to_word(self):
        assert pysafe("+") == "plus"

    def test_empty_becomes_underscore(self):
        assert pysafe("") == "_"

    def test_dotted_keyword_part(self):
        assert pysafe("a.class.b") == "a.class_.b"


class TestSnakeCase:
    # snakecase is a faithful port of coquilib's WIP implementation. Its
    # uppercase handler replaces each [A-Z] match with match[1:].lower() — for a
    # single-char match that is the empty string, so an interior uppercase letter
    # is DROPPED rather than lowercased-with-underscore. These assertions pin the
    # ported behavior exactly (quirks included), not an idealized snake_case.
    def test_separators_normalized_to_underscore(self):
        assert snakecase("some name-here") == "some_name_here"

    def test_already_snake_is_stable(self):
        assert snakecase("some_name") == "some_name"

    def test_leading_digit_prefixed_with_underscore(self):
        assert snakecase("1abc") == "_1abc"

    def test_interior_uppercase_is_dropped(self):
        # Documents the WIP quirk: "CamelCase" -> "camelase" (the 'C' of "Case"
        # is consumed, no underscore inserted).
        assert snakecase("CamelCase") == "camelase"


class TestCamelCase:
    def test_underscore_join(self):
        assert camelcase("some_name") == "SomeName"

    def test_dotted(self):
        assert camelcase("a.b.c") == "ABC"

    def test_explicit_single_separator(self):
        assert camelcase("a-b", separators="-") == "AB"

    def test_empty_passthrough(self):
        assert camelcase("") == ""

    def test_snake_then_camel(self):
        # snakecase normalizes separators; camelcase re-joins on them. A dotted
        # name survives the pair intact (no interior-uppercase quirk here).
        assert camelcase(snakecase("some.name")) == "SomeName"
