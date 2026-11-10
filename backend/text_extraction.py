import codecs
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz


MAX_EXTRACTED_TEXT_CHARS = 1_000_000


@dataclass(frozen=True)
class TextExtractionResult:
    status: str
    extracted_text: str | None
    message: str


def _decode_text_file(content: bytes) -> str:
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return content.decode("utf-32")
    if content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return content.decode("utf-16")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def _limited_text(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_EXTRACTED_TEXT_CHARS:
        return text, False
    return text[:MAX_EXTRACTED_TEXT_CHARS], True


def extract_text_file(path: Path) -> TextExtractionResult:
    try:
        content = path.read_bytes()
    except OSError:
        return TextExtractionResult("error", None, "The stored evidence file could not be read.")
    if not content:
        return TextExtractionResult("empty", None, "The TXT file is empty.")

    try:
        text = _decode_text_file(content)
    except UnicodeError:
        return TextExtractionResult("error", None, "The TXT file uses an unsupported text encoding.")
    if not text.strip():
        return TextExtractionResult("empty", None, "No readable text was found in the TXT file.")
    control_characters = sum(
        ord(character) < 32 and character not in "\t\n\r\f" for character in text
    )
    if control_characters / len(text) > 0.02:
        return TextExtractionResult("error", None, "The TXT file does not appear to contain readable text.")

    text, truncated = _limited_text(text)
    message = "Text extracted successfully."
    if truncated:
        message = "Text extracted. The preview is limited to the first 1,000,000 characters."
    return TextExtractionResult("extracted", text, message)


def extract_pdf_file(path: Path) -> TextExtractionResult:
    try:
        content = path.read_bytes()
    except OSError:
        return TextExtractionResult("error", None, "The stored evidence file could not be read.")
    if not content:
        return TextExtractionResult("empty", None, "The PDF file is empty.")

    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                return TextExtractionResult(
                    "password_protected", None, "This PDF is password-protected and cannot be extracted."
                )
            if document.page_count == 0:
                return TextExtractionResult("empty", None, "The PDF has no pages.")

            page_text = [page.get_text("text") for page in document]
            text = "\n".join(page_text).strip()
            if not text:
                has_images = any(page.get_images(full=True) for page in document)
                if has_images:
                    return TextExtractionResult(
                        "ocr_required", None, "This PDF appears to contain scanned pages. OCR is required."
                    )
                return TextExtractionResult("empty", None, "No readable text was found in the PDF.")
    except (fitz.FileDataError, RuntimeError, ValueError, OSError):
        return TextExtractionResult("error", None, "The PDF is corrupted or could not be read.")

    text, truncated = _limited_text(text)
    message = "Text extracted successfully."
    if truncated:
        message = "Text extracted. The preview is limited to the first 1,000,000 characters."
    return TextExtractionResult("extracted", text, message)


def extract_evidence_text(path: Path, extension: str) -> TextExtractionResult:
    if extension == ".pdf":
        return extract_pdf_file(path)
    if extension == ".txt":
        return extract_text_file(path)
    return TextExtractionResult(
        "unsupported", None, "Text extraction is currently supported for PDF and TXT files only."
    )
