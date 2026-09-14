from app.matching import AssociationMatcher


def test_high_clear_match_auto_associates():
    matcher = AssociationMatcher(high=0.70, low=0.30, margin_min=0.10)
    result = matcher.match(
        "Garden Quest Roblox guide",
        ["Garden Quest Roblox", "Space Factory Tycoon"],
        dense_scores=[0.95, 0.05],
    )
    assert result.outcome == "auto_associate"
    assert result.winner_index == 0


def test_small_margin_routes_to_review():
    matcher = AssociationMatcher(high=0.20, low=0.05, margin_min=0.20)
    result = matcher.match("pet simulator", ["pet simulator alpha", "pet simulator beta"])
    assert result.outcome == "review"
    assert "ambiguous_margin" in result.rationale


def test_weak_match_abstains():
    matcher = AssociationMatcher(high=0.80, low=0.60, margin_min=0.10)
    result = matcher.match("medieval bakery", ["space combat arena"])
    assert result.outcome == "no_match"
    assert result.winner_index is None

