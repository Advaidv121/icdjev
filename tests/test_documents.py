import base64
import builtins
import io
import random
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from icdjev.documents import DocumentError, PreparedImage, pdf_to_base64_png, prepare_documents


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "image.png"
    Image.new("RGB", (320, 160), "red").save(path)
    return path


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "document.pdf"
    with pdfium.PdfDocument.new() as document:
        for width, height in [(200, 100), (100, 300), (80, 80)]:
            page = document.new_page(width, height)
            page.close()
        document.save(path)
    return path


def decoded(item):
    return Image.open(io.BytesIO(base64.b64decode(item.base64, validate=True)))


def test_prepared_image_is_frozen_and_metadata_omits_payload(image_path):
    item = prepare_documents(image_path)[0]
    assert isinstance(item, PreparedImage)
    assert item.metadata() == {
        "source": str(image_path),
        "page": None,
        "width": 320,
        "height": 160,
        "content_type": "image/png",
        "byte_size": len(base64.b64decode(item.base64)),
    }
    assert item.to_data_url() == "data:image/png;base64," + item.base64
    with pytest.raises(FrozenInstanceError):
        item.width = 1


def test_inputs_and_pdf_page_selection_preserve_order(image_path, pdf_path):
    results = prepare_documents([image_path, pdf_path, image_path], pages=[3, 1])
    assert [item.source for item in results] == [
        str(image_path),
        str(pdf_path),
        str(pdf_path),
        str(image_path),
    ]
    assert [item.page for item in results] == [None, 3, 1, None]
    assert [(item.width, item.height) for item in results[1:3]] == [(2048, 2048), (2048, 1024)]


def test_unoptimized_default_pdf_still_fits_service_limit(pdf_path):
    item = prepare_documents(pdf_path, pages=[2], compress=False)[0]
    assert item.height == 2048
    assert item.byte_size < 4 * 1024**2


def test_pdf_helper_returns_raw_png_base64(pdf_path):
    values = pdf_to_base64_png(pdf_path, pages=[2], target_longest_edge=600)
    assert len(values) == 1
    assert not values[0].startswith("data:")
    with Image.open(io.BytesIO(base64.b64decode(values[0], validate=True))) as image:
        assert image.format == "PNG"
        assert image.size == (200, 600)


@pytest.mark.parametrize("compress", [True, False])
def test_resizing_is_independent_of_compression(image_path, compress):
    item = prepare_documents(image_path, target_longest_edge=100, compress=compress)[0]
    assert (item.width, item.height) == (100, 50)
    assert decoded(item).size == (100, 50)


def test_compression_is_lossless_and_opt_out_is_larger(image_path):
    compressed = prepare_documents(str(image_path), compress=True)[0]
    plain = prepare_documents(image_path, compress=False)[0]
    assert compressed.byte_size < plain.byte_size
    assert decoded(compressed).tobytes() == decoded(plain).tobytes()
    assert (compressed.width, compressed.height) == (320, 160)  # no upscaling


@pytest.mark.parametrize("pages", [[0], [-1], [4], [1, 1], [True], [1.5], [], "1"])
def test_invalid_pdf_page_selections(pdf_path, pages):
    with pytest.raises(DocumentError, match="page|Page"):
        prepare_documents(pdf_path, pages=pages)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_longest_edge": 0},
        {"target_longest_edge": True},
        {"target_longest_edge": 1.5},
        {"max_pages": 0},
        {"max_pages": True},
        {"compress": "yes"},
    ],
)
def test_invalid_options(image_path, kwargs):
    with pytest.raises(DocumentError):
        prepare_documents(image_path, **kwargs)


def test_page_cap_checked_before_rendering(pdf_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("rendering occurred before checking page limit")

    monkeypatch.setattr(pdfium.PdfPage, "render", forbidden)
    with pytest.raises(DocumentError, match="max_pages"):
        prepare_documents(pdf_path, max_pages=2)
    with pytest.raises(DocumentError, match="max_pages"):
        prepare_documents([pdf_path, pdf_path], pages=[1, 2], max_pages=3)


def test_subset_can_fit_page_cap(pdf_path):
    assert len(prepare_documents(pdf_path, pages=[3], max_pages=1)) == 1


def test_pixel_limit_before_decode_or_render(image_path, pdf_path, monkeypatch):
    with pytest.raises(DocumentError, match="pixel"):
        prepare_documents(pdf_path, pages=[1], target_longest_edge=10000)
    original_open = Image.open

    def oversized(*args, **kwargs):
        image = original_open(*args, **kwargs)
        image._size = (4001, 4000)
        return image

    monkeypatch.setattr(Image, "open", oversized)
    with pytest.raises(DocumentError, match="pixel"):
        prepare_documents(image_path, target_longest_edge=10)


def test_decoded_png_byte_limit_is_not_base64_length(tmp_path):
    path = tmp_path / "noise.png"
    rng = random.Random(7)
    Image.frombytes("RGB", (1200, 1200), rng.randbytes(1200 * 1200 * 3)).save(path)
    with pytest.raises(DocumentError, match="4 MiB"):
        prepare_documents(path, compress=False)
    with pytest.raises(DocumentError, match="4 MiB"):
        prepare_documents(path, compress=True)


@pytest.mark.parametrize("failure", [Image.DecompressionBombError, Image.DecompressionBombWarning])
def test_decoder_bombs_are_sanitized(image_path, monkeypatch, failure):
    def bomb(*args, **kwargs):
        raise failure("secret-content")

    monkeypatch.setattr(Image, "open", bomb)
    with pytest.raises(DocumentError) as caught:
        prepare_documents(image_path)
    assert "secret-content" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("name", ["missing.png", "bad.pdf", "bad.png", "unsupported.txt"])
def test_file_errors_are_sanitized(tmp_path, name):
    path = tmp_path / name
    if not name.startswith("missing"):
        path.write_text("patient-secret")
    with pytest.raises(DocumentError) as caught:
        prepare_documents(path)
    assert "patient-secret" not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_remote_url_is_rejected():
    with pytest.raises(DocumentError, match="local"):
        prepare_documents("https://example.com/secret.png")


def test_empty_input():
    assert prepare_documents([]) == []


def test_pdf_render_errors_are_sanitized(pdf_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("patient-secret")

    monkeypatch.setattr(pdfium.PdfPage, "render", fail)
    with pytest.raises(DocumentError, match="render") as caught:
        prepare_documents(pdf_path)
    assert "patient-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_transparent_and_exif_rotated_images(tmp_path):
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (40, 20), (255, 0, 0, 20)).save(path)
    assert decoded(prepare_documents(path)[0]).getpixel((0, 0)) == (255, 0, 0, 20)
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (40, 20), "red").save(path, exif=exif)
    item = prepare_documents(path)[0]
    assert (item.width, item.height) == (20, 40)


def test_office_timeout_is_sanitized_and_cleaned(tmp_path, monkeypatch):
    path = tmp_path / "report.docx"
    path.write_bytes(b"office")
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/soffice")
    roots = []

    def timeout(command, **kwargs):
        roots.append(Path(command[-1]).parent)
        raise subprocess.TimeoutExpired(command, 60, output="patient-secret")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(DocumentError, match="convert") as caught:
        prepare_documents(path)
    assert "patient-secret" not in str(caught.value)
    assert all(not root.exists() for root in roots)


def test_office_callback_preserves_original_source(tmp_path, pdf_path):
    path = tmp_path / "report.docx"
    path.write_bytes(b"office")
    received = []

    def convert(source):
        received.append(source)
        return pdf_path

    item = prepare_documents(path, pages=[2], office_converter=convert)[0]
    assert received == [path]
    assert item.source == str(path)
    assert item.page == 2


@pytest.mark.parametrize("failure", [RuntimeError, DocumentError])
def test_office_callback_failures_are_sanitized(tmp_path, failure):
    path = tmp_path / "report.doc"
    path.write_bytes(b"office")

    def convert(source):
        raise failure("patient-secret")

    with pytest.raises(DocumentError, match="convert") as caught:
        prepare_documents(path, office_converter=convert)
    assert "patient-secret" not in str(caught.value)


def test_missing_libreoffice_has_actionable_error(tmp_path, monkeypatch):
    path = tmp_path / "report.docx"
    path.write_bytes(b"office")
    monkeypatch.setattr("shutil.which", lambda command: None)
    with pytest.raises(DocumentError, match="LibreOffice|office_converter"):
        prepare_documents(path)


def test_default_office_conversion_is_isolated_and_cleaned(tmp_path, pdf_path, monkeypatch):
    path = tmp_path / "report.docx"
    path.write_bytes(b"office")
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/soffice")
    roots = []

    def run(command, **kwargs):
        assert kwargs["timeout"] > 0
        assert not kwargs.get("shell", False)
        assert kwargs["stdout"] == subprocess.DEVNULL
        profile = next(arg for arg in command if arg.startswith("-env:UserInstallation="))
        assert "file:" in profile
        outdir = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        assert source != path
        roots.append(outdir.parent)
        (outdir / source.with_suffix(".pdf").name).write_bytes(pdf_path.read_bytes())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    for _ in range(2):
        assert len(prepare_documents(path, pages=[1])) == 1
    assert roots[0] != roots[1]
    assert all(not root.exists() for root in roots)


def test_missing_optional_dependency_is_actionable(image_path, monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "PIL":
            raise ImportError("secret")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(DocumentError, match=r"icdjev\[documents\]"):
        prepare_documents(image_path)
