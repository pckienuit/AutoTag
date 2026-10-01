from backend.app.caption import build_instruction, expected_case_type, validate_annotation
from backend.app.models import RcrAnnotation, SubjectAssignment
from backend.app.text_cleanup import cleanup_select_text, cleanup_target_condition


def annotation(case_type: str, texts: list[str], condition: str, ids=(("1",), ("2",))) -> RcrAnnotation:
    subjects = [SubjectAssignment(subject_id=i + 1, identity_ids=list(group)) for i, group in enumerate(ids)]
    if case_type in {"INDIVIDUAL", "GROUP"}:
        subjects = subjects[:1]
    return RcrAnnotation(case_type=case_type, subjects=subjects, select_texts=texts, target_condition=condition)


def test_build_instruction_one_subject_matches_page_preview() -> None:
    result = build_instruction(
        annotation("INDIVIDUAL", ["the man in a dark suit."], "Subject 1 is holding a diploma.")
    )

    assert result == (
        "Identify Subject 1 as the man in a dark suit; "
        "then retrieve target images where Subject 1 is holding a diploma."
    )


def test_build_instruction_two_subjects_and_placeholder() -> None:
    result = build_instruction(annotation("RELATIONAL", ["the man", ""], "Subject 1 is shaking hands with Subject 2"))

    assert result == (
        "Identify Subject 1 as the man and Subject 2 as […]; "
        "then retrieve target images where Subject 1 is shaking hands with Subject 2."
    )


def test_build_instruction_strips_repeated_template_prefix() -> None:
    result = build_instruction(
        annotation("INDIVIDUAL", ["a man"], "then retrieve target images where Subject 1 is sitting")
    )

    assert result.endswith("where Subject 1 is sitting.")


def test_valid_annotations_have_no_issues() -> None:
    assert validate_annotation(annotation("INDIVIDUAL", ["a man in red"], "Subject 1 is sitting"), ["1"]) == []
    assert (
        validate_annotation(
            annotation("DUAL", ["a man", "a woman"], "Subject 1 is sitting and Subject 2 is standing"),
            ["1", "2"],
        )
        == []
    )
    assert (
        validate_annotation(
            annotation("GROUP", ["the two men"], "the members of Subject 1 are standing", ids=(("1", "2"),)),
            ["1", "2"],
        )
        == []
    )


def test_target_must_mention_the_right_subjects() -> None:
    one = validate_annotation(annotation("INDIVIDUAL", ["a man"], "the man is sitting"))
    two = validate_annotation(annotation("DUAL", ["a man", "a woman"], "Subject 1 is sitting"))
    extra = validate_annotation(annotation("INDIVIDUAL", ["a man"], "Subject 1 is next to Subject 2"))

    assert "Target condition must mention Subject 1." in one
    assert "Target condition must mention Subject 2." in two
    assert "Target condition must not mention Subject 2." in extra


def test_select_text_rules() -> None:
    issues = validate_annotation(
        annotation("DUAL", ["Subject 1 in red", "người phụ nữ"], "Subject 1 sits and Subject 2 stands")
    )

    assert "Subject 1 SELECT must not contain the word 'Subject'." in issues
    assert "Subject 2 SELECT must be English (ASCII) text." in issues
    assert validate_annotation(annotation("INDIVIDUAL", [""], "Subject 1 sits")) == [
        "Subject 1 SELECT text is empty."
    ]


def test_subject_assignment_rules() -> None:
    assert "Identity assigned twice: 1." in validate_annotation(
        annotation("DUAL", ["a", "b"], "Subject 1 sits and Subject 2 stands", ids=(("1",), ("1",)))
    )
    assert "Unknown identity for Subject 1: 9." in validate_annotation(
        annotation("INDIVIDUAL", ["a man"], "Subject 1 sits", ids=(("9",),)), ["1"]
    )
    assert "GROUP requires Subject 1 to have at least 2 identities." in validate_annotation(
        annotation("GROUP", ["the men"], "the members of Subject 1 sit")
    )
    assert "INDIVIDUAL requires Subject 1 to have exactly 1 identity." in validate_annotation(
        annotation("INDIVIDUAL", ["a man"], "Subject 1 sits", ids=(("1", "2"),))
    )


def test_expected_case_type_follows_identity_count() -> None:
    group = [{"subject_id": 1, "identity_ids": ["1", "2"]}]
    single = [{"subject_id": 1, "identity_ids": ["1"]}]

    assert expected_case_type(group, "INDIVIDUAL") == "GROUP"
    assert expected_case_type(single, "GROUP") == "INDIVIDUAL"
    assert expected_case_type(group, "RELATIONAL") == "RELATIONAL"


def test_cleanup_keeps_subject_labels_in_target() -> None:
    assert cleanup_select_text("a man in red, and holding a bag.") == "a man in red and holding a bag"
    assert cleanup_target_condition("Subject 1 is sitting, and Subject 2 is standing.") == (
        "Subject 1 is sitting and Subject 2 is standing"
    )
