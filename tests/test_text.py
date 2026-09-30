"""Tests for duho.text (expand, pysafe, snakecase, camelcase, kebabcase)."""

import keyword

import duho.text as text
import pytest
from duho.text import camelcase, expand, kebabcase, pysafe, snakecase


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

    # 0.5.4's own outputs, captured by running its (recursive) expand() --
    # the leftmost range varies fastest and the rightmost slowest. The
    # iterative (itertools.product) rewrite changed that order; it must not.
    @pytest.mark.parametrize(
        "template,main_output",
        [
            ("x[1-2][a-b]", ["x1a", "x2a", "x1b", "x2b"]),
            (
                "x[1-2][a-b][P-Q]",
                [
                    "x1aP",
                    "x2aP",
                    "x1bP",
                    "x2bP",
                    "x1aQ",
                    "x2aQ",
                    "x1bQ",
                    "x2bQ",
                ],
            ),
            (
                "x[0-1][0-1][0-1]",
                [
                    "x000",
                    "x100",
                    "x010",
                    "x110",
                    "x001",
                    "x101",
                    "x011",
                    "x111",
                ],
            ),
        ],
    )
    def test_multi_range_order_matches_0_5_4(self, template, main_output):
        assert list(expand(template)) == main_output

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

    def test_trailing_symbol_is_spelled_out_twice(self):
        # Matches duho 0.5.4 exactly, quirk included: a trailing symbol is
        # consumed by the startswith/endswith handling AND by the interior
        # `replace` that follows it, so it is spelled out twice
        # ("a+" -> "aplus_plus", not the more obvious "a_plus"). A later
        # branch "fixed" this to "a_plus"/"x_not", which was itself the
        # regression -- 0.5.4 never produced that.
        assert pysafe("a+") == "aplus_plus"
        assert pysafe("x!") == "xnot_not"

    def test_leading_and_trailing_symbol_both_handled(self):
        assert pysafe("+x+") == "plus_xplus_plus"

    def test_symbol_substitution_runs_on_the_whole_dotted_string(self):
        # The symbol substitution operates on the joined string, not on each
        # separator-split part in isolation -- matching 0.5.4, where a symbol
        # at a part boundary can affect its neighbor ("a.+" -> "a.plus_plus",
        # not "a.plus"). A later branch scoped this to one part at a time,
        # which was itself the regression.
        assert pysafe("a.+") == "a.plus_plus"
        assert pysafe("x+") == "xplus_plus"

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


class TestPysafeVs054:
    """Every value here was produced by running duho 0.5.4's own ``pysafe``.

    Where 0.5.4 already returned a valid, non-keyword identifier for each of
    its dotted parts (``main_valid=True``), this branch must return the exact
    same string -- including 0.5.4's own quirks. Where 0.5.4's result was
    itself invalid, this branch may differ, but must still produce a valid
    identifier in every dotted part.
    """

    TABLE = [
        ("a+", ".", "aplus_plus", True),
        ("+a", ".", "plus_a", True),
        ("+", ".", "plus", True),
        ("!x", ".", "not_x", True),
        ("x!", ".", "xnot_not", True),
        ("a*", ".", "aall_all", True),
        ("a.+b", ".", "a.plusb", True),
        ("+.b", ".", "plus_.b", True),
        ("1abc", ".", "1abc", False),
        ("a.1b", ".", "a.1b", False),
        ("a$b", ".", "a$b", False),
        ("a.b-c", ".", "a.b_c", True),
        ("not", ".", "not_", True),
        ("a.not", ".", "a.not_", True),
        ("!", ".", "not", False),
        ("x.!", ".", "x.not_not", True),
        ("é", ".", "é", True),
        ("", ".", "_", True),
        ("a..b", ".", "a..b", False),
        ("class.+", ".", "class_.plus_plus", True),
        ("a+.b+", ".", "aplus.bplus_plus", True),
        ("3", ".", "3", False),
        ("a b.c", ".", "a_b.c", True),
        ("a b", " ", "a_b", True),
        ("a-b", "-", "a_b", True),
    ]

    @pytest.mark.parametrize("value,separator,main_output,main_valid", TABLE)
    def test_matches_0_5_4_or_improves_on_an_invalid_result(
        self, value, separator, main_output, main_valid
    ):
        result = pysafe(value, separator=separator)
        if main_valid:
            assert result == main_output
        else:
            for part in result.split(separator):
                assert part.isidentifier(), (value, result, part)

    def test_non_dot_separator_does_not_leak_into_the_output(self):
        # 0.5.4's `-`/space-to-`_` substitution runs on the WHOLE joined
        # string, so it also converts the separator itself when the
        # separator IS `-` or a space. A branch that instead substituted
        # per-part (splitting on the separator first) left the separator
        # character sitting untouched in the output.
        assert pysafe("a b", separator=" ") == "a_b"
        assert pysafe("a-b", separator="-") == "a_b"

    def test_empty_separator_still_raises(self):
        with pytest.raises(ValueError):
            pysafe("ab", separator="")


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


class TestKebabCase:
    """Plan 38's acronym-aware kebab-case rule -- the one behind duho's
    class-derived command names and a field's default long flag."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("BuildPyz", "build-pyz"),
            ("ShowHTTPStatus", "show-http-status"),
            ("PyTrueNAS", "py-true-nas"),
            ("Ipv4Tool", "ipv4-tool"),
            ("LeakCheck", "leak-check"),
            ("Convert", "convert"),
            ("_Private", "private"),
            ("already-kebab", "already-kebab"),
        ],
    )
    def test_known_mappings(self, value, expected):
        assert kebabcase(value) == expected

    def test_empty_returns_empty(self):
        assert kebabcase("") == ""

    def test_unlike_snakecase_an_acronym_run_stays_together(self):
        # snakecase lowers an acronym letter-by-letter; kebabcase keeps the
        # run together up to its last letter.
        assert snakecase("HTTPServer") == "h_t_t_p_server"
        assert kebabcase("HTTPServer") == "http-server"

    def test_collision_sibling_classes_kebab_to_the_same_name(self):
        # `FooBar` and `Foo_Bar` are meant to collide (see args.py's build-time
        # duplicate-name guard) -- both must resolve to the identical string.
        assert kebabcase("FooBar") == kebabcase("Foo_Bar") == "foo-bar"

    def test_already_lowercase_snake_case_is_unaffected(self):
        assert kebabcase("dry_run") == "dry-run"

    def test_leading_and_trailing_underscores_never_leak_a_dash(self):
        assert kebabcase("_Leading") == "leading"
        assert kebabcase("Trailing_") == "trailing"
        assert kebabcase("__Both__") == "both"

    def test_acronym_then_word_and_lower_then_upper_boundaries(self):
        assert kebabcase("HTTPPort") == "http-port"
        assert kebabcase("testMe") == "test-me"


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
