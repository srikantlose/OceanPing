import pytest

from app.modules.scoring.service import apply_verification


def test_apply_verification_rejects_unknown_action():
    with pytest.raises(ValueError):
        apply_verification(db=None, report=object(), analyst="a", action="ignore")


def test_apply_verification_rejects_unknown_corrected_hazard_type():
    # Validation happens before any report/db access, so bare stand-ins are fine here.
    with pytest.raises(ValueError):
        apply_verification(
            db=None,
            report=object(),
            analyst="a",
            action="reject",
            corrected_hazard_type="not_a_real_hazard_type",
        )
