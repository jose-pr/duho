"""Tests for duho.text (expand, pysafe, snakecase, camelcase)."""

import keyword

import duho.text as text
import pytest
from duho.text import camelcase, expand, pysafe, snakecase


class TestExpand:
    def test_digit_range_not_zero_padded(self):
        # By design, digit-range output is NOT zero-padded (int() drops zeros).
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

    def test_many_ranges_do_not_hit_the_recursion_limit(self):
        # The old recursive implementation hit RecursionError around 1000
        # sequential ranges; the iterative (itertools.product) rewrite has no
        # such bound.
        text_with_many_ranges = "[1-1]" * 1500
        assert list(expand(text_with_many_ranges)) == ["1" * 1500]

    def test_reversed_digit_range_raises(self):
        # A reversed range used to silently yield nothing -- a typo then reads
        # as "no targets, and run_targets([]) returns 0", i.e. success.
        with pytest.raises(ValueError):
            list(expand("web[10-01]"))
        with pytest.raises(ValueError):
            list(expand("h[3-1]"))

    def test_reversed_letter_range_raises(self):
        with pytest.raises(ValueError):
            list(expand("h[C-A]"))

    def test_mismatched_kind_endpoints_raise(self):
        with pytest.raises(ValueError):
            list(expand("h[a-1]"))
        with pytest.raises(ValueError):
            list(expand("h[1-a]"))

    def test_multi_character_letter_endpoint_raises(self):
        # Used to raise a raw TypeError from ord() on a 2-char string.
        with pytest.raises(ValueError):
            list(expand("h[AB-CD]"))

    def test_mixed_case_letter_range_raises(self):
        # Used to silently walk the ASCII punctuation between 'Z' and 'a'.
        with pytest.raises(ValueError):
            list(expand("n[B-a]"))


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

    def test_trailing_symbol_is_not_duplicated(self):
        # Used to call removeprefix where removesuffix was meant, so the
        # trailing symbol survived and was then spelled out a second time
        # by the interior replace ("a+" -> "aplus_plus").
        assert pysafe("a+") == "a_plus"
        assert pysafe("x!") == "x_not"

    def test_leading_and_trailing_symbol_both_handled(self):
        assert pysafe("+x+") == "plus_x_plus"

    def test_symbol_only_affects_its_own_dotted_part(self):
        # The prefix/suffix rules apply per separator-split part, not to the
        # whole dotted string -- "a.+" used to become "a.plus_plus".
        assert pysafe("a.+") == "a.plus"
        assert pysafe("x+") == "x_plus"

    def test_leading_digit_is_prefixed(self):
        # Never documented as valid, but pysafe promises a valid identifier.
        assert pysafe("1abc") == "_1abc"
        assert pysafe("1abc").isidentifier()

    def test_symbol_substitution_producing_a_keyword_is_still_suffixed(self):
        # PYREPLACE["!"] == "not", a reserved keyword; the keyword check must
        # run AFTER symbol substitution, or pysafe("!") returns "not" itself.
        assert pysafe("!") == "not_"
        assert not keyword.iskeyword(pysafe("!"))

    def test_other_punctuation_is_underscored(self):
        assert pysafe("a/b") == "a_b"
        assert pysafe("a$b") == "a_b"

    def test_empty_dotted_part_becomes_underscore(self):
        assert pysafe("a..b") == "a._.b"

    def test_result_parts_are_always_valid_identifiers(self):
        for value in ("1password", "!", "x!", "a+", "c++", "class", "a..b", ""):
            for part in pysafe(value).split("."):
                assert part.isidentifier(), (value, pysafe(value), part)


class TestSnakeCase:
    def test_separators_normalized_to_underscore(self):
        assert snakecase("some name-here") == "some_name_here"

    def test_already_snake_is_stable(self):
        assert snakecase("some_name") == "some_name"

    def test_leading_digit_prefixed_with_underscore(self):
        assert snakecase("1abc") == "_1abc"

    def test_interior_uppercase_lowered_with_underscore(self):
        # An interior uppercase letter is lowercased WITH an underscore,
        # not dropped. "CamelCase" -> "camel_case".
        assert snakecase("CamelCase") == "camel_case"

    def test_empty_returns_empty(self):
        assert snakecase("") == ""

    def test_camel_snake_roundtrip(self):
        assert camelcase(snakecase("CamelCaseName")) == "CamelCaseName"

    def test_title_case_does_not_double_underscore(self):
        # An uppercase letter right after a separator used to get a SECOND
        # underscore inserted ("My-App" -> "my__app").
        assert snakecase("My-App") == "my_app"
        assert snakecase("Some Name") == "some_name"

    def test_kebab_case_does_not_double_underscore(self):
        assert snakecase("get-User-Id") == "get_user_id"

    def test_dotted_does_not_double_underscore(self):
        assert snakecase("Foo.Bar") == "foo_bar"

    def test_leading_underscore_survives_without_doubling(self):
        assert snakecase("_Private") == "_private"

    def test_single_underscore_before_uppercase_stays_single(self):
        assert snakecase("a_B") == "a_b"


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

    def test_trailing_separator(self):
        # A trailing separator yields an empty final segment; empty parts must be
        # skipped, not indexed (part[0] on "" would raise IndexError). This is hit
        # in practice: pysafe turns a keyword like `global` into `global_`, then
        # camelcasing that name lands on the trailing `_`.
        assert camelcase("global_") == "Global"
        assert camelcase("x_") == "X"

    def test_doubled_and_leading_separator(self):
        assert camelcase("a__b") == "AB"
        assert camelcase("_a") == "A"


class TestModuleAll:
    def test_range_not_exported_on_star_import(self):
        # `range`/`unicode_range` shadow the builtin *inside this module by
        # design*, but listing them in __all__ used to export that shadowing
        # to `from duho.text import *`, breaking every later `range(n)` for a
        # star-importer.
        assert "range" not in text.__all__
        assert "unicode_range" not in text.__all__

    def test_range_still_reachable_as_a_module_attribute(self):
        # Only the star-import surface changed -- `duho.text.range` itself is
        # unchanged and still callable.
        assert list(text.range("1", "3")) == ["1", "2", "3"]
