"""The roster arrives as configuration now, not source. It must fail loudly and
completely rather than seeding half a family."""
import base64
import json

import pytest

from app.data.family_seed_loader import SeedInvalid, load_seed_document, parse_seed_document


def test_parses_the_sample_document(seed_doc_raw):
    doc = parse_seed_document(seed_doc_raw)
    assert doc.version == 1
    assert len(doc.members) == 6


def test_accepts_base64(seed_doc_raw):
    encoded = base64.b64encode(seed_doc_raw.encode()).decode()
    assert len(parse_seed_document(encoded).members) == 6


def test_tolerates_surrounding_whitespace(seed_doc_raw):
    assert len(parse_seed_document(f"\n  {seed_doc_raw}  \n").members) == 6


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", "{", "[]"])
def test_rejects_junk(raw):
    with pytest.raises(SeedInvalid):
        parse_seed_document(raw)


def test_rejects_wrong_version(seed_doc_raw):
    doc = json.loads(seed_doc_raw)
    doc["version"] = 2
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_rejects_a_non_iso_date(seed_doc_raw):
    doc = json.loads(seed_doc_raw)
    doc["members"][0]["birthday"] = "08/07/1983"
    with pytest.raises(SeedInvalid) as exc:
        parse_seed_document(json.dumps(doc))
    assert "birthday" in str(exc.value)


def test_rejects_a_compact_date(seed_doc_raw):
    """date.fromisoformat would silently accept "19830807"."""
    doc = json.loads(seed_doc_raw)
    doc["members"][0]["birthday"] = "19830807"
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_rejects_an_unknown_field(seed_doc_raw):
    doc = json.loads(seed_doc_raw)
    doc["members"][0]["favourite_colour"] = "blue"
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_rejects_has_circle_access(seed_doc_raw):
    """create_family_member passes kwargs straight onto the model, so a seed
    entry could otherwise grant family-circle SMS access on a new row."""
    doc = json.loads(seed_doc_raw)
    doc["members"][0]["has_circle_access"] = True
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_rejects_duplicate_names(seed_doc_raw):
    doc = json.loads(seed_doc_raw)
    doc["members"].append({"name": "dominic rivers", "relationship": "son"})
    with pytest.raises(SeedInvalid) as exc:
        parse_seed_document(json.dumps(doc))
    assert "duplicate" in str(exc.value).lower()


def test_rejects_an_unresolvable_parent(seed_doc_raw):
    """A typo'd parent silently left parent_id null forever."""
    doc = json.loads(seed_doc_raw)
    doc["members"][3]["parent"] = "Nobody At All"
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_rejects_an_empty_name(seed_doc_raw):
    doc = json.loads(seed_doc_raw)
    doc["members"][0]["name"] = "   "
    with pytest.raises(SeedInvalid):
        parse_seed_document(json.dumps(doc))


def test_unknown_participant_is_a_warning_not_an_error(seed_doc_raw):
    """An activity can legitimately involve someone who isn't a family member."""
    doc = json.loads(seed_doc_raw)
    doc["activities"][0]["participant_names"].append("A Cousin")
    assert parse_seed_document(json.dumps(doc)) is not None


def test_unset_configuration_returns_none(mocker):
    """Not an error: an already-seeded database needs no seed document."""
    mocker.patch("app.config.settings.family_seed_json", "")
    mocker.patch("app.config.settings.family_seed_path", "")
    assert load_seed_document() is None


def test_loads_from_the_env_var(mocker, seed_doc_raw):
    mocker.patch("app.config.settings.family_seed_json", seed_doc_raw)
    assert len(load_seed_document().members) == 6


def test_null_fields_never_overwrite(seed_doc_raw):
    """to_kwargs excludes None so a null in the document can't NULL a column."""
    doc = parse_seed_document(seed_doc_raw)
    wren = next(m for m in doc.members if m.name == "Wren Rivers")
    assert wren.birthday is None
    assert "birthday" not in wren.to_kwargs()
    assert "parent" not in wren.to_kwargs()
