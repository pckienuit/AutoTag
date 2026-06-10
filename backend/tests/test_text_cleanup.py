from backend.app.caption import build_caption
from backend.app.models import Stage2Annotation, SubjectAnnotation
from backend.app.text_cleanup import remove_comma_before_connectors


def test_remove_comma_before_connectors() -> None:
    assert remove_comma_before_connectors("wearing blue jeans, and holding a bag") == "wearing blue jeans and holding a bag"
    assert remove_comma_before_connectors("red, or blue") == "red or blue"


def test_build_caption_avoids_comma_before_and() -> None:
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

    assert ", and" not in caption.lower()
