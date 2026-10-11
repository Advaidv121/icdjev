from pathlib import Path

import pytest

from icdjev import Catalogue, UnknownCodeError, UnsupportedFormatError

FIXTURE = Path(__file__).parent / "data" / "mini_claml.xml"


@pytest.fixture(scope="module")
def cat():
    return Catalogue.load(FIXTURE)


def test_edition(cat):
    assert cat.edition.uid == "icd10_test" and cat.edition.version == "test"


def test_modifier_children_are_virtual_and_reachable(cat):
    # T11 has no <SubClass>; its subdivisions only exist through the modifier.
    assert [n.code for n in cat.children("T11")] == ["T11.0", "T11.9"]
    n = cat.get("T11.0")
    assert n.virtual and n.modifier == "MDIAB_4" and n.assignable
    assert n.full_label == "Test diabetes, with complications"
    assert [x.code for x in cat.path("T11.0")] == ["T1", "T10-T19", "T11", "T11.0"]


def test_validate_rejects_non_assignable(cat):
    assert cat.validate("T11.9") == (True, "ok")
    ok, why = cat.validate("T11")
    assert not ok and "T11.0" in why
    assert not cat.validate("T10-T19")[0]
    assert cat.validate("NOPE")[1] == "not in this edition"


def test_fifth_character_modifier_is_extension_not_child(cat):
    assert [n.code for n in cat.children("T12")] == ["T12.0", "T12.1"]
    assert cat.get("T12.0").extensions == ("0=closed", "1=open")
    assert cat.get("T12.0").children == []


def test_exclusions_machine_and_free_text(cat):
    own = cat.exclusion_targets("T11", inherited=False)
    assert [r.raw for _, r in own] == ["P70.2", "T12.-"]
    assert [r.wildcard for _, r in own] == [False, True]
    assert [r.text for _, r in cat.free_text_exclusions("T11") if _ == "T11"] == [
        "see other chapters for localized conditions"]


def test_chapter_exclusions_are_inherited(cat):
    inherited = cat.exclusion_targets("T13")  # T13 has none of its own
    assert [r.raw for _, r in inherited] == ["O00-O99"]
    assert cat.exclusion_targets("T13", inherited=False) == []


def test_label_text_is_not_fused(cat):
    # ElementTree.itertext would produce "neonatal sugar diseaseP70.2"
    assert cat.get("T11").exclusions[0].text == "neonatal sugar disease P70.2"


def test_dagger_asterisk_link(cat):
    assert cat.get("G99").usage == "aster"
    assert [r.start for r in cat.paired("G99")] == ["T11"]
    assert cat.get("T11.0").usage == "dagger"


def test_in_range(cat):
    ref = cat.exclusion_targets("T13")[0][1]  # O00-O99
    assert cat.in_range(ref, "O24.1") and not cat.in_range(ref, "P01")
    wild = cat.exclusion_targets("T11", inherited=False)[1][1]  # T12.-
    assert cat.in_range(wild, "T12.1") and not cat.in_range(wild, "T13")


def test_inclusion_terms_are_searchable(cat):
    assert cat.search("high blood pressure")[0].code == "T13"
    assert cat.search("sugar disease")[0].code in {"T11", "T11.0", "T11.9"}


def test_unknown_code(cat):
    with pytest.raises(UnknownCodeError):
        cat.get("ZZZ")


def test_rejects_non_who_files(tmp_path):
    bad = tmp_path / "x.xml"
    bad.write_text('<ClaML version="2.0.0"><Identifier authority="CDC" uid="icd10cm"/>'
                   '<Title name="x" version="1" date="d">t</Title></ClaML>')
    with pytest.raises(UnsupportedFormatError):
        Catalogue.load(bad)
    junk = tmp_path / "y.xml"
    junk.write_text("<html/>")
    with pytest.raises(UnsupportedFormatError):
        Catalogue.load(junk)


def test_parse_ref_dash_variants():
    from icdjev.refs import parse_ref

    for raw in ("N18.3 - N18.9", "N18.3 \u2013 N18.9", "N18.3\u2014N18.9"):
        r = parse_ref(raw)
        assert (r.start, r.end, r.is_range) == ("N18.3", "N18.9", True), raw
    w = parse_ref("E12.-")
    assert w.wildcard and w.start == "E12"
    assert not parse_ref("see body system-related chapters")
