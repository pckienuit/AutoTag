from backend.app.caption import build_caption, validate_annotation
from backend.app.models import Stage2Annotation, SubjectAnnotation


def test_single_caption() -> None:
    annotation = Stage2Annotation(
        caseType="SINGLE",
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["26"],
                descQueryFinal="a person wearing a black jacket",
                changeTargetFinal="is laughing on an outdoor bench",
            )
        ],
    )
    assert build_caption(annotation) == (
        "In the query image, Subject 1 refers to a person wearing a black jacket. "
        "Retrieve target images where Subject 1 is laughing on an outdoor bench."
    )
    assert validate_annotation(annotation) == []


def test_multi_caption() -> None:
    annotation = Stage2Annotation(
        caseType="MULTI",
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["193"],
                descQueryFinal="the man standing at the far left",
                changeTargetFinal="is rowing a boat",
            ),
            SubjectAnnotation(
                subjectId=2,
                queryGroupIds=["411"],
                descQueryFinal="the person kneeling on the grass",
                changeTargetFinal="is holding up a fish",
            ),
        ],
    )
    assert build_caption(annotation) == (
        "In the query image, Subject 1 refers to the man standing at the far left "
        "and Subject 2 refers to the person kneeling on the grass. "
        "Retrieve target images where Subject 1 is rowing a boat and Subject 2 is holding up a fish."
    )
    assert validate_annotation(annotation) == []


def test_relational_caption() -> None:
    annotation = Stage2Annotation(
        caseType="RELATIONAL",
        pairChangeFinal="is taking a photo of",
        relationalSubject2ChangeEnabled=True,
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["308"],
                descQueryFinal="the fourth seated person from the right",
            ),
            SubjectAnnotation(
                subjectId=2,
                queryGroupIds=["307"],
                descQueryFinal="the third seated person from the right",
                changeTargetFinal="is sitting on a blue lounge chair",
            ),
        ],
    )
    caption = build_caption(annotation)
    assert "Subject 1 refers to the fourth seated person from the right and Subject 2 refers to" in caption
    assert "Subject 1 is taking a photo of Subject 2" in caption
    assert "with Subject 2 is sitting on a blue lounge chair" in caption
    assert validate_annotation(annotation) == []


def test_relational_caption_both_extras() -> None:
    annotation = Stage2Annotation(
        caseType="RELATIONAL",
        pairChangeFinal="is taking a photo of",
        relationalSubject1ChangeEnabled=True,
        relationalSubject2ChangeEnabled=True,
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["308"],
                descQueryFinal="the fourth seated person from the right",
                changeTargetFinal="is standing up",
            ),
            SubjectAnnotation(
                subjectId=2,
                queryGroupIds=["307"],
                descQueryFinal="the third seated person from the right",
                changeTargetFinal="is sitting on a blue lounge chair",
            ),
        ],
    )
    caption = build_caption(annotation)
    assert "Subject 1 is taking a photo of Subject 2" in caption
    assert "with Subject 1 is standing up and Subject 2 is sitting on a blue lounge chair" in caption
    assert validate_annotation(annotation) == []


def test_relational_caption_no_extras() -> None:
    annotation = Stage2Annotation(
        caseType="RELATIONAL",
        pairChangeFinal="is taking a photo of",
        relationalSubject1ChangeEnabled=False,
        relationalSubject2ChangeEnabled=False,
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["308"],
                descQueryFinal="the fourth seated person from the right",
            ),
            SubjectAnnotation(
                subjectId=2,
                queryGroupIds=["307"],
                descQueryFinal="the third seated person from the right",
            ),
        ],
    )
    caption = build_caption(annotation)
    assert caption == (
        "In the query image, Subject 1 refers to the fourth seated person from the right "
        "and Subject 2 refers to the third seated person from the right. "
        "Retrieve target images where Subject 1 is taking a photo of Subject 2."
    )
    assert validate_annotation(annotation) == []
