"""Local document preparation with optional Pillow/PDFium dependencies.

PDFs are rendered at the requested longest edge (2048 by default). Images are
never upscaled. Compression controls lossless PNG encoding effort, not resizing.
Each output must fit 16 million pixels and 4 MiB of decoded PNG bytes; oversized
outputs are rejected, never silently resized. DOC/DOCX conversion uses an
explicit callback or an installed LibreOffice with a private temporary profile.
Only the first frame of an image file is prepared. No network URLs are accepted.
"""

import base64 as _base64
import io
import math
import os
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Callable, Sequence
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["DocumentError", "PreparedImage", "prepare_documents", "pdf_to_base64_png"]

_MAX_PIXELS = 16_000_000
_MAX_BYTES = 4 * 1024**2
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


class DocumentError(RuntimeError):
    """Invalid document input, missing dependency, conversion or rendering failure."""


@dataclass(frozen=True)
class PreparedImage:
    """PNG payload and provenance; page is 1-based for PDFs, None for images."""

    source: str
    page: int | None
    width: int
    height: int
    content_type: str
    base64: str = field(repr=False)
    byte_size: int

    def to_data_url(self) -> str:
        return f"data:{self.content_type};base64,{self.base64}"

    def metadata(self) -> dict[str, Any]:
        """Return provenance and dimensions without the sensitive image payload."""
        return {
            "source": self.source,
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "content_type": self.content_type,
            "byte_size": self.byte_size,
        }


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise DocumentError(
            "Install document dependencies with pip install 'icdjev[documents]'."
        ) from None
    return Image, ImageOps


def _pdfium():
    try:
        import pypdfium2
    except ImportError:
        raise DocumentError(
            "Install document dependencies with pip install 'icdjev[documents]'."
        ) from None
    return pypdfium2


def _positive_integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DocumentError(f"{name} must be a positive integer.")


def _local_path(value: Any) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise DocumentError("Documents must be local filesystem paths.")
    try:
        path = Path(value)
        if "://" in str(value):
            raise DocumentError("Documents must be local filesystem paths, not URLs.")
        if not path.is_file():
            raise DocumentError("Document does not exist or is not a regular local file.")
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("Could not access local document.") from None
    return path


def _pixels(width: int, height: int) -> None:
    if width < 1 or height < 1 or width * height > _MAX_PIXELS:
        raise DocumentError("Image exceeds the 16 million pixel limit or has invalid dimensions.")


def _size(width: float, height: float, edge: int, *, upscale: bool) -> tuple[int, int]:
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise DocumentError("Document has invalid dimensions.")
    scale = edge / max(width, height)
    if not upscale:
        scale = min(scale, 1)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode(image, source: Path, page: int | None, compress: bool) -> PreparedImage:
    _pixels(*image.size)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=compress, compress_level=9 if compress else 6)
    payload = output.getvalue()
    if len(payload) > _MAX_BYTES:
        raise DocumentError("Prepared PNG exceeds the 4 MiB image limit; request a smaller edge.")
    return PreparedImage(
        source=str(source),
        page=page,
        width=image.width,
        height=image.height,
        content_type="image/png",
        base64=_base64.b64encode(payload).decode("ascii"),
        byte_size=len(payload),
    )


@contextmanager
def _office_pdf(source: Path):
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable is None:
        raise DocumentError("Install LibreOffice or provide an office_converter callback.")
    # tempfile honors TMPDIR; neither filenames nor profile state are shared.
    with tempfile.TemporaryDirectory(prefix="icdjev-office-") as directory:
        root = Path(directory)
        output = root / "output"
        output.mkdir()
        local = root / ("input" + source.suffix.lower())
        shutil.copyfile(source, local)
        command = [
            executable,
            f"-env:UserInstallation={(root / 'profile').as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output),
            str(local),
        ]
        result = subprocess.run(
            command,
            shell=False,
            timeout=60,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        converted = output / "input.pdf"
        if result.returncode != 0 or not converted.is_file():
            raise DocumentError("Could not convert office document to PDF.")
        yield converted


def _prepare_image(source: Path, edge: int, compress: bool) -> PreparedImage:
    Image, ImageOps = _pillow()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                # Validate before load or resizing, even if the requested output is small.
                _pixels(*original.size)
                with ImageOps.exif_transpose(original) as oriented:
                    size = _size(*oriented.size, edge, upscale=False)
                    with oriented.convert(
                        "RGBA"
                        if "A" in oriented.getbands() or "transparency" in oriented.info
                        else "RGB"
                    ) as image:
                        if image.size != size:
                            with image.resize(size, Image.Resampling.LANCZOS) as resized:
                                return _encode(resized, source, None, compress)
                        return _encode(image, source, None, compress)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("Could not decode or prepare image safely.") from None


def prepare_documents(
    paths: str | os.PathLike | Sequence[str | os.PathLike],
    *,
    target_longest_edge: int = 2048,
    compress: bool = True,
    pages: Sequence[int] | None = None,
    max_pages: int = 100,
    office_converter: Callable[[Path], Path] | None = None,
) -> list[PreparedImage]:
    """Prepare local images, PDFs and DOC/DOCX in input order.

    ``pages`` is a nonempty sequence of unique 1-based PDF page numbers; its
    order is preserved and the selection applies to every PDF (including office
    conversions), not images. ``max_pages`` caps total selected pages/images
    across all inputs, checked before any rendering. Callbacks own their output
    file cleanup. Compression is lossless and opt-out does not disable resizing.
    """
    _positive_integer(target_longest_edge, "target_longest_edge")
    _positive_integer(max_pages, "max_pages")
    if not isinstance(compress, bool):
        raise DocumentError("compress must be a boolean.")
    if office_converter is not None and not callable(office_converter):
        raise DocumentError("office_converter must be callable.")
    selection = None
    if pages is not None:
        if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)) or not pages:
            raise DocumentError("pages must be a nonempty sequence of unique page numbers.")
        selection = list(pages)
        for page in selection:
            _positive_integer(page, "page")
        if len(set(selection)) != len(selection):
            raise DocumentError("Duplicate page numbers are not allowed.")
    if isinstance(paths, (str, os.PathLike)):
        inputs = [paths]
    elif isinstance(paths, Sequence) and not isinstance(paths, bytes):
        inputs = list(paths)
    else:
        raise DocumentError("Documents must be a local path or sequence of local paths.")

    plan = []
    total = 0
    with ExitStack() as stack:
        for value in inputs:
            source = _local_path(value)
            suffix = source.suffix.lower()
            if suffix in _IMAGE_SUFFIXES:
                plan.append((source, None, None, None))
                total += 1
            elif suffix in {".pdf", ".doc", ".docx"}:
                pdf_path = source
                if suffix != ".pdf":
                    if office_converter is not None:
                        try:
                            converted = office_converter(source)
                        except Exception:
                            raise DocumentError(
                                "Could not convert office document to PDF."
                            ) from None
                    else:
                        try:
                            converted = stack.enter_context(_office_pdf(source))
                        except DocumentError:
                            raise
                        except Exception:
                            raise DocumentError(
                                "Could not convert office document to PDF."
                            ) from None
                    pdf_path = _local_path(converted)
                    if pdf_path.suffix.lower() != ".pdf":
                        raise DocumentError("Office converter must return a local PDF path.")
                pdfium = _pdfium()
                try:
                    document = stack.enter_context(pdfium.PdfDocument(pdf_path))
                    count = len(document)
                    selected = selection if selection is not None else range(1, count + 1)
                    if count < 1 or any(page > count for page in selected):
                        raise DocumentError("Requested PDF page is outside the document.")
                    total += len(selected)
                    if total > max_pages:
                        raise DocumentError("Selected documents exceed max_pages.")
                    for number in selected:
                        with closing(document[number - 1]) as page:
                            dimensions = _size(*page.get_size(), target_longest_edge, upscale=True)
                            _pixels(*dimensions)
                        plan.append((source, document, number, dimensions))
                except DocumentError:
                    raise
                except Exception:
                    raise DocumentError("Could not open or inspect PDF document.") from None
            else:
                raise DocumentError("Unsupported document type; use an image, PDF, DOC or DOCX.")
            if total > max_pages:
                raise DocumentError("Selected documents exceed max_pages.")

        results = []
        for source, document, number, dimensions in plan:
            if document is None:
                results.append(_prepare_image(source, target_longest_edge, compress))
                continue
            Image, _ = _pillow()
            try:
                with closing(document[number - 1]) as page:
                    scale = target_longest_edge / max(page.get_size())
                    # PDFium rounds render dimensions up; check allocation size too.
                    _pixels(*(math.ceil(dimension * scale) for dimension in page.get_size()))
                    with closing(page.render(scale=scale)) as bitmap:
                        with bitmap.to_pil() as image:
                            if image.size != dimensions:
                                with image.resize(dimensions, Image.Resampling.LANCZOS) as resized:
                                    results.append(_encode(resized, source, number, compress))
                            else:
                                results.append(_encode(image, source, number, compress))
            except DocumentError:
                raise
            except Exception:
                raise DocumentError("Could not render or prepare PDF page safely.") from None
        return results


def pdf_to_base64_png(
    path: str | os.PathLike,
    *,
    pages: Sequence[int] | None = None,
    target_longest_edge: int = 2048,
    compress: bool = True,
    max_pages: int = 100,
) -> list[str]:
    """Render a local PDF to raw PNG base64 strings, one per selected page."""
    source = _local_path(path)
    if source.suffix.lower() != ".pdf":
        raise DocumentError("pdf_to_base64_png requires a local PDF file.")
    return [
        item.base64
        for item in prepare_documents(
            source,
            pages=pages,
            target_longest_edge=target_longest_edge,
            compress=compress,
            max_pages=max_pages,
        )
    ]
