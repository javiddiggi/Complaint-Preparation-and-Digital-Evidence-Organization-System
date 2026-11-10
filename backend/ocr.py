import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError
from pytesseract.pytesseract import TesseractError, TesseractNotFoundError
from dotenv import load_dotenv

from text_extraction import MAX_EXTRACTED_TEXT_CHARS


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MAX_OCR_IMAGE_PIXELS = 20_000_000
MAX_OCR_IMAGE_DIMENSION = 8_000
MAX_OCR_PDF_PAGES = 20
OCR_DPI = 180
OCR_TIMEOUT_SECONDS = 30
OCR_CONFIG = "--oem 3 --psm 6"


@dataclass(frozen=True)
class OcrResult:
    status: str
    extracted_text: str | None
    message: str


def _failure(message: str, status: str = "error") -> OcrResult:
    return OcrResult(status, None, message)


def _tesseract_error() -> str | None:
    configured_path = os.getenv("TESSERACT_CMD")
    if configured_path:
        executable = shutil.which(configured_path)
        if executable is None:
            return (
                "The TESSERACT_CMD environment variable does not point to a usable Tesseract "
                "executable. Install Tesseract OCR and set TESSERACT_CMD to its executable."
            )
        pytesseract.pytesseract.tesseract_cmd = executable

    try:
        pytesseract.get_tesseract_version()
    except TesseractNotFoundError:
        return (
            "Tesseract OCR is not installed or is not on PATH. Install the Windows build from "
            "https://github.com/UB-Mannheim/tesseract/wiki, then set TESSERACT_CMD to the "
            "installed tesseract.exe path before starting the API."
        )
    except (OSError, subprocess.SubprocessError, TesseractError) as error:
        return f"Tesseract OCR could not be started: {error}"
    return None


def _validate_image_dimensions(image: Image.Image) -> None:
    width, height = image.size
    if (
        width <= 0
        or height <= 0
        or width > MAX_OCR_IMAGE_DIMENSION
        or height > MAX_OCR_IMAGE_DIMENSION
        or width * height > MAX_OCR_IMAGE_PIXELS
    ):
        raise ValueError(
            "The image dimensions exceed the OCR limit of 8,000 pixels per side "
            "or 20 megapixels."
        )


def _preprocess_image(image: Image.Image, upscale: bool = True) -> Image.Image:
    _validate_image_dimensions(image)
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    _validate_image_dimensions(image)

    grayscale = np.asarray(image.convert("L"))
    if upscale and max(width, height) < 1_200:
        scale = min(2, MAX_OCR_IMAGE_DIMENSION // max(width, height))
        scale = min(scale, math.floor(math.sqrt(MAX_OCR_IMAGE_PIXELS / (width * height))))
        if scale > 1:
            grayscale = cv2.resize(
                grayscale,
                (width * scale, height * scale),
                interpolation=cv2.INTER_CUBIC,
            )
    blurred = cv2.GaussianBlur(grayscale, (3, 3), 0)
    _, thresholded = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    return Image.fromarray(thresholded)


def _run_ocr(image: Image.Image) -> str:
    prepared = _preprocess_image(image)
    return pytesseract.image_to_string(
        prepared,
        config=OCR_CONFIG,
        timeout=OCR_TIMEOUT_SECONDS,
    )


def _limited_result(text: str) -> OcrResult:
    text = text.strip()
    if not text:
        return _failure("OCR completed, but no readable text was found.", "empty")
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        return OcrResult(
            "ocr_extracted",
            text[:MAX_EXTRACTED_TEXT_CHARS],
            "OCR completed. The saved text is limited to the first 1,000,000 characters.",
        )
    return OcrResult("ocr_extracted", text, "OCR text extracted successfully.")


def _extract_image(path: Path) -> OcrResult:
    try:
        with Image.open(path) as image:
            expected_format = "PNG" if path.suffix.lower() == ".png" else "JPEG"
            if image.format != expected_format:
                return _failure("The image contents do not match the file extension.")
            _validate_image_dimensions(image)
            image.load()
            prepared = _preprocess_image(image)
            if error := _tesseract_error():
                return _failure(error)
            return _limited_result(
                pytesseract.image_to_string(
                    prepared,
                    config=OCR_CONFIG,
                    timeout=OCR_TIMEOUT_SECONDS,
                )
            )
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        RuntimeError,
        Image.DecompressionBombError,
        cv2.error,
    ) as error:
        return _failure(f"The image is corrupted or cannot be processed: {error}")
    except TesseractNotFoundError:
        return _failure(
            "Tesseract OCR is not installed or is not on PATH. Install the Windows build from "
            "https://github.com/UB-Mannheim/tesseract/wiki, then set TESSERACT_CMD to the "
            "installed tesseract.exe path before starting the API."
        )
    except (TesseractError, RuntimeError) as error:
        return _failure(f"OCR could not be completed: {error}")


def _pdf_page_image(page: fitz.Page) -> Image.Image:
    rect = page.rect
    width = max(1, round(rect.width * OCR_DPI / 72))
    height = max(1, round(rect.height * OCR_DPI / 72))
    scale = min(
        1.0,
        MAX_OCR_IMAGE_DIMENSION / max(width, height),
        math.sqrt(MAX_OCR_IMAGE_PIXELS / (width * height)),
    )
    matrix = fitz.Matrix(OCR_DPI / 72 * scale, OCR_DPI / 72 * scale)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _extract_pdf(path: Path) -> OcrResult:
    try:
        document = fitz.open(path)
    except (fitz.FileDataError, RuntimeError, ValueError, OSError):
        return _failure("The PDF is corrupted or could not be read.")

    with document:
        if document.needs_pass:
            return _failure(
                "This PDF is password-protected and cannot be processed with OCR.",
                "password_protected",
            )
        try:
            page_count = document.page_count
        except (fitz.FileDataError, RuntimeError, ValueError, OSError):
            return _failure("The PDF is corrupted or could not be read.")
        if page_count == 0:
            return _failure("The PDF has no pages.", "empty")
        if page_count > MAX_OCR_PDF_PAGES:
            return _failure(
                f"The PDF has {page_count} pages; OCR is limited to "
                f"{MAX_OCR_PDF_PAGES} pages."
            )

        try:
            page_texts = [page.get_text("text").strip() for page in document]
        except (fitz.FileDataError, RuntimeError, ValueError, OSError):
            return _failure("The PDF is corrupted or could not be read.")
        if not any(not text for text in page_texts):
            return _failure(
                "This PDF contains selectable text and does not need OCR. Use Extract text instead.",
                "unsupported",
            )
        if error := _tesseract_error():
            return _failure(error)

        extracted_pages = []
        try:
            for page, page_text in zip(document, page_texts):
                if page_text:
                    extracted_pages.append(page_text)
                else:
                    extracted_pages.append(_run_ocr(_pdf_page_image(page)))
        except (
            fitz.FileDataError,
            RuntimeError,
            ValueError,
            OSError,
            cv2.error,
        ) as error:
            return _failure(f"A PDF page could not be processed: {error}")
        except TesseractNotFoundError:
            return _failure(
                "Tesseract OCR is not installed or is not on PATH. Install the Windows build from "
                "https://github.com/UB-Mannheim/tesseract/wiki, then set TESSERACT_CMD to the "
                "installed tesseract.exe path before starting the API."
            )
        except TesseractError as error:
            return _failure(f"OCR could not be completed: {error}")

        return _limited_result("\n\n".join(extracted_pages))


def extract_evidence_ocr(path: Path, extension: str) -> OcrResult:
    if extension in {".png", ".jpg", ".jpeg"}:
        if not path.is_file():
            return _failure("The stored evidence file could not be read.")
        return _extract_image(path)
    if extension == ".pdf":
        if not path.is_file():
            return _failure("The stored evidence file could not be read.")
        return _extract_pdf(path)
    return _failure("OCR is supported for PNG, JPG, JPEG, and scanned PDF evidence.", "unsupported")
