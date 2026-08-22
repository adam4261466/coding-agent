"""Hard exclusions run BEFORE scoring; scored prospects must never include them."""

from linkedin_intelligence.ingest.scorer import apply_hard_exclusions, score
from linkedin_intelligence.ingest.parsers import ConnectionRecord


def _prospect(conn, role_cat="software_engineering", comp_type="startup"):
    return {
        "prospect_id": "p_x",
        "full_name": conn.full_name,
        "raw_position": conn.position,
        "normalized_company": "foo",
        "role_category": role_cat,
        "company_type": comp_type,
        "total_score": 99.0,
        "segments": [],
        "selection_reasons": [],
        "research_mode": None,
        "evidence": [],
    }


def test_ignore_company_is_excluded_even_at_max_score(config):
    icp = config["icp"]
    ignore = icp["hard_exclusions"]["ignore_companies"][0]
    p = _prospect(ConnectionRecord("A", "B", "u"))
    p["normalized_company"] = ignore
    kept, excluded = apply_hard_exclusions([p], config)
    assert kept == []
    assert excluded[0]["reasons"] == ["company on ignore list"]


def test_unknown_role_with_no_position_is_excluded(config):
    p = _prospect(ConnectionRecord("A", "B", "u"), role_cat="unknown")
    p["raw_position"] = None
    kept, excluded = apply_hard_exclusions([p], config)
    assert excluded[0]["reasons"] == ["no position available"]


def test_no_position_but_known_category_is_kept(config):
    p = _prospect(ConnectionRecord("A", "B", "u"), role_cat="software_engineering")
    p["raw_position"] = None
    kept, excluded = apply_hard_exclusions([p], config)
    assert len(kept) == 1


def test_scored_prospects_never_include_excluded(config):
    icp = config["icp"]
    ignore = icp["hard_exclusions"]["ignore_companies"][0]
    bad = _prospect(ConnectionRecord("X", "Y", "u"))
    bad["normalized_company"] = ignore
    good = _prospect(ConnectionRecord("G", "H", "u"))
    kept, excluded = score([bad, good], config)
    assert all(p["normalized_company"] != ignore for p in kept)
    assert len(kept) == 1
