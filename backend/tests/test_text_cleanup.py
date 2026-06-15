from backend.app.caption import build_caption
from backend.app.models import Stage2Annotation, SubjectAnnotation
from backend.app.text_cleanup import cleanup_desc_change_text, remove_comma_before_connectors


def test_remove_comma_before_connectors() -> None:
    assert remove_comma_before_connectors("wearing blue jeans, and holding a bag") == "wearing blue jeans and holding a bag"
    assert remove_comma_before_connectors("red, or blue") == "red or blue"


def test_cleanup_desc_change_text_removes_subject_references() -> None:
    assert cleanup_desc_change_text("Subject 1 refers to the man in a red shirt") == "the man in a red shirt"
    assert cleanup_desc_change_text("Subject 2 is sitting, and smiling") == "is sitting and smiling"
    assert cleanup_desc_change_text("the two people in Subject 1 are standing") == "the two people are standing"


def test_build_caption_keeps_canonical_subject_comma_only() -> None:
    caption = build_caption(
        Stage2Annotation(
            caseType="MULTI",
            subjects=[
                SubjectAnnotation(
                    subjectId=1,
                    queryGroupIds=["1"],
                    descQueryFinal="the man in a hat",
                    changeTargetFinal="is sitting, and smiling",
                ),
                SubjectAnnotation(
                    subjectId=2,
                    queryGroupIds=["2"],
                    descQueryFinal="the woman in red",
                    changeTargetFinal="is standing",
                ),
            ],
        )
    )

    assert "hat, and Subject 2 refers" in caption
    assert "sitting, and smiling" not in caption
    assert "sitting and smiling" in caption
