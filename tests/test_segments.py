"""Segment assignment runs before research and never drops a prospect."""

from linkedin_intelligence.ingest.segmentation import assign_segments, load_segment_defs


def _p(role=None, comp=None, total=60, rel=0, convos=0, eng=0):
    return {
        "prospect_id": id,
        "role_category": role,
        "company_type": comp,
        "total_score": total,
        "relationship_score": rel,
        "conversation_count": convos,
        "engagement_score": eng,
        "segments": [],
    }


def test_warm_relationship_segment_matches_anyone_with_conversations():
    defs = load_segment_defs()
    p = _p(role="unknown", total=10, convos=3)
    assign_segments([p], defs)
    assert "warm_relationships" in p["segments"]


def test_multiple_segments_can_match_at_once():
    defs = load_segment_defs()
    p = _p(role="data", comp="startup", total=70, eng=50)
    assign_segments([p], defs)
    assert "data_high_icp" in p["segments"]
    assert "document_heavy_problem" in p["segments"]


def test_unsegmented_is_an_explicit_default():
    defs = load_segment_defs()
    p = _p(role="unknown", comp="unknown", total=10)
    assign_segments([p], defs)
    assert p["segments"] == ["unsegmented"]


def test_segment_index_maps_names_to_prospect_ids():
    defs = load_segment_defs()
    a = _p(role="data", comp="startup", total=70)
    b = _p(role="ai_ml", comp="startup", total=66)
    idx = assign_segments([a, b], defs)
    assert set(idx.keys()) == {"data_high_icp", "ai_ml_high_icp",
                               "document_heavy_problem"}


def test_unsegmented_gets_status_and_missing_signals():
    defs = load_segment_defs()
    p = _p(role="unknown", comp="unknown", total=10)
    assign_segments([p], defs)
    assert p["segmentation_status"] == "unsegmented"
    assert p["missing_signals"]  # explains WHY it did not segment


def test_segmented_has_no_missing_signals():
    defs = load_segment_defs()
    p = _p(role="data", comp="startup", total=70)
    assign_segments([p], defs)
    assert p["segmentation_status"] == "segmented"
    assert p["missing_signals"] == []
