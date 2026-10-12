from pathlib import Path
from types import SimpleNamespace

import pytest

from icdjev import ICDLookup

XML = Path(__file__).parent / "data" / "mini_claml.xml"


class ImageJudge:
    def __init__(self):
        self.seen = []

    def judge(self, state, questions, *, images=None):
        self.seen.append(images)
        return SimpleNamespace(
            answers={
                key: {"noul": 0.99 if value["code"] == "T13" else 0.01}
                for key, value in state["candidates"].items()
            },
            model="fake-clef",
            usage={"input_tokens": 10, "output_tokens": 0},
        )

    def close(self):
        pass


def test_images_reach_traversal_and_verification_without_persisting():
    judge = ImageJudge()
    with ICDLookup(catalogue_path=XML, backend="clef", judge=judge) as lookup:
        result = lookup.get_icd("High blood pressure", images=["synthetic-image"])
        assert [item.code for item in result] == ["T13"]
        assert all(item == ["synthetic-image"] for item in judge.seen)
        judge.seen.clear()
        lookup.get_icd("High blood pressure")
        assert all(item is None for item in judge.seen)


def test_jev_rejects_images_before_judgment():
    judge = ImageJudge()
    with ICDLookup(catalogue_path=XML, judge=judge) as lookup:
        with pytest.raises(ValueError, match="Clef"):
            lookup.get_icd("High blood pressure", images=["synthetic-image"])
    assert judge.seen == []


@pytest.mark.parametrize("model", ["", False, 0, []])
def test_explicit_invalid_model_is_not_defaulted(model):
    from icdjev.clef import ClefError

    with pytest.raises(ClefError, match="Model"):
        ICDLookup(
            catalogue_path=XML,
            backend="clef",
            model=model,
            cloudflare_account_id="0" * 32,
            cloudflare_api_token="synthetic",
        )


def test_large_jev_custom_judge_batch_stays_compatible():
    with ICDLookup(catalogue_path=XML, batch_size=128, judge=ImageJudge()) as lookup:
        assert lookup.batch_size == 128


def test_invalid_backend_rejected():
    with pytest.raises(ValueError, match="backend"):
        ICDLookup(catalogue_path=XML, backend="unknown", judge=ImageJudge())


def test_document_batches_keep_provenance_and_scores_separate(tmp_path):
    from PIL import Image

    files = []
    for number in range(5):
        path = tmp_path / f"page-{number}.png"
        Image.new("RGB", (50, 100), "white").save(path)
        files.append(path)
    judge = ImageJudge()
    with ICDLookup(catalogue_path=XML, backend="clef", judge=judge) as lookup:
        report = lookup.get_icd_from_documents(files, text="High blood pressure")
    assert [len(batch.sources) for batch in report.batches] == [4, 1]
    assert all([m.code for m in b.review.matches] == ["T13"] for b in report.batches)
    assert report.needs_review and report.warnings
    assert report.request_count == len(judge.seen)
    assert report.usage["input_tokens"] == 10 * report.request_count
    assert "base64" not in str(report.to_dict())
    assert [s["source"] for b in report.batches for s in b.sources] == [str(p) for p in files]


def test_empty_document_lookup_rejected_without_judgment():
    judge = ImageJudge()
    with ICDLookup(catalogue_path=XML, backend="clef", judge=judge) as lookup:
        with pytest.raises(ValueError, match="At least one"):
            lookup.get_icd_from_documents([])
    assert judge.seen == []


def test_document_batch_byte_limit(monkeypatch):
    import icdjev.documents
    from icdjev import PreparedImage

    images = [
        PreparedImage(str(i), None, 1, 1, "image/png", "synthetic", 3 * 1024**2) for i in range(3)
    ]
    monkeypatch.setattr(icdjev.documents, "prepare_documents", lambda *args, **kwargs: images)
    with ICDLookup(catalogue_path=XML, backend="clef", judge=ImageJudge()) as lookup:
        report = lookup.get_icd_from_documents(["placeholder"], text="High blood pressure")
    assert [len(batch.sources) for batch in report.batches] == [2, 1]


def test_documents_auto_select_clef_without_changing_text_backend(monkeypatch, tmp_path):
    from PIL import Image

    import icdjev.clef

    image = tmp_path / "note.png"
    Image.new("RGB", (50, 100), "white").save(image)
    clef_judge = ImageJudge()
    monkeypatch.setattr(icdjev.clef, "ClefClient", lambda **kwargs: clef_judge)
    text_judge = ImageJudge()
    with ICDLookup(catalogue_path=XML, judge=text_judge) as lookup:
        report = lookup.get_icd_from_documents([image], text="High blood pressure")
        assert report.batches[0].review.matches[0].code == "T13"
        assert lookup.backend == "jev"
    assert text_judge.seen == []
