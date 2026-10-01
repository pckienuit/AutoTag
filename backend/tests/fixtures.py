from typing import Any


def box(identity_id: str, x: float = 0.1, y: float = 0.1, width: float = 0.2, height: float = 0.3) -> dict:
    return {"identity_id": identity_id, "x": x, "y": y, "width": width, "height": height}


def make_task(case_type: str = "INDIVIDUAL", sample_id: str = "s1", revision: int = 1) -> dict[str, Any]:
    two = case_type in {"DUAL", "RELATIONAL"}
    ids = ["10", "20"] if (two or case_type == "GROUP") else ["10"]
    if case_type == "GROUP":
        initial = [{"subject_id": 1, "identity_ids": ids}]
    elif two:
        initial = [{"subject_id": 1, "identity_ids": ["10"]}, {"subject_id": 2, "identity_ids": ["20"]}]
    else:
        initial = [{"subject_id": 1, "identity_ids": ["10"]}]
    return {
        "sample_id": sample_id,
        "case_type": case_type,
        "split": "TEST",
        "status": "ASSIGNED",
        "revision": revision,
        "query": {"image_id": "q", "image_url": f"/api/image/{sample_id}/query", "boxes": [box(i) for i in ids]},
        "target": {"image_id": "t", "image_url": f"/api/image/{sample_id}/target", "boxes": [box(i) for i in ids]},
        "candidate_identity_ids": ids,
        "initial_subjects": initial,
    }
