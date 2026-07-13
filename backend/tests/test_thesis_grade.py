"""thesis_grade.grade() — AI yön çağrısını gerçekleşen getiriye göre notlama (SAF)."""

from app.engine.thesis_grade import grade


def test_grade_up_hit():
    assert grade("up", 0.05) == ("hit", 0.05)


def test_grade_up_miss():
    assert grade("up", -0.05)[0] == "miss"


def test_grade_down_hit():
    assert grade("down", -0.05)[0] == "hit"


def test_grade_down_miss():
    assert grade("down", 0.05)[0] == "miss"


def test_grade_neutral_band_small_move():
    # |getiri| < %1 -> çözülmedi -> neutral (gürültüyü isabet sayma)
    assert grade("up", 0.005)[0] == "neutral"
    assert grade("down", -0.008)[0] == "neutral"


def test_grade_nondirectional_is_neutral():
    assert grade("neutral", 0.10)[0] == "neutral"
    assert grade("mixed", -0.10)[0] == "neutral"
    assert grade(None, 0.10)[0] == "neutral"


def test_grade_no_data():
    assert grade("up", None) == ("no_data", None)
