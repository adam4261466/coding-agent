"""Scoring is deterministic and keeps dimensions separate (never one opaque number)."""

from linkedin_intelligence.ingest.scorer import build_prospect, recompute_total


def test_same_input_same_scores(config, conn):
    a = build_prospect(conn, [], [], config)
    b = build_prospect(conn, [], [], config)
    score_keys = ("role_fit", "company_fit", "relationship_score",
                  "engagement_score", "problem_fit_score", "buying_signal_score",
                  "relevance_score", "total_score")
    assert {k: a[k] for k in score_keys} == {k: b[k] for k in score_keys}


def test_problem_fit_is_unknown_before_evidence(config, conn):
    p = build_prospect(conn, [], [], config)
    assert p["problem_fit_score"] is None
    assert p["problem_fit_confidence"] is None


def test_all_dimensions_are_present(config, conn):
    p = build_prospect(conn, [], [], config)
    for key in ("role_fit", "company_fit", "relationship_score",
                "engagement_score", "problem_fit_score", "buying_signal_score",
                "commercial_intent", "relevance_score", "total_score",
                "problem_affinity_hint", "buying_signal_confidence",
                "conversation_intelligence"):
        assert key in p, key


def test_total_is_weighted_sum_of_components(config, conn):
    icp = config["icp"]
    p = build_prospect(conn, [], [], config)
    w = icp["weights"]["total"]
    expected = (w["relevance"] * p["relevance_score"]
                + w["relationship"] * p["relationship_score"]
                + w["engagement"] * p["engagement_score"]
                + w["buying_signal"] * p["buying_signal_score"])
    assert p["total_score"] == round(expected, 1)


def test_problem_fit_blends_into_total_when_known(config, conn):
    icp = config["icp"]
    p = build_prospect(conn, [], [], config)
    p["problem_fit_score"] = 90
    p["problem_fit_confidence"] = 0.8
    recompute_total(p, icp)
    blend = icp["weights"]["problem_fit_blend_when_known"]
    expected = (1 - blend) * p["relevance_score"] * icp["weights"]["total"]["relevance"] \
        + blend * 90
    base = (icp["weights"]["total"]["relevance"] * p["relevance_score"]
            + icp["weights"]["total"]["relationship"] * p["relationship_score"]
            + icp["weights"]["total"]["engagement"] * p["engagement_score"]
            + icp["weights"]["total"]["buying_signal"] * p["buying_signal_score"])
    assert p["total_score"] == round((1 - blend) * base + blend * 90, 1)
    assert expected  # keep the local referenced to avoid lint noise
