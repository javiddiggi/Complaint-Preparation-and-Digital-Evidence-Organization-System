import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


_DATE_PATTERN = re.compile(
    r"\b(?:"
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s?[AP]M)?\b", re.IGNORECASE)
_CONVERSATION_TIME_PATTERN = re.compile(
    r"(?<!\d)(?:0?[1-9]|1[0-2]):[0-5]\d\s?[AP]\.?M\.?(?!\w)|"
    r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)",
    re.IGNORECASE,
)
_AMOUNT_PATTERN = re.compile(
    r"(?:(?:₹|INR|Rs\.?|USD|\$|EUR|€)\s?[\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_TRANSACTION_PATTERN = re.compile(
    r"\b(?:UTR|transaction(?:\s+(?:ID|reference|ref))?|order(?:\s+(?:ID|number|no\.?))?)"
    r"\s*(?:number|no\.?|id|reference|ref)?\s*[:#-]\s*([A-Z0-9][A-Z0-9/-]{3,})",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "about", "after", "also", "and", "are", "before", "been", "between", "could",
    "for", "from", "have", "help", "into", "more", "need", "please", "that", "the",
    "their", "there", "this", "what", "when", "where", "which", "with", "your",
}


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]


def _source_excerpt(sentence: str, max_length: int = 500) -> str:
    normalized = " ".join(sentence.split())
    return normalized[:max_length]


def extract_structured_information(
    text: str,
    evidence_id: str,
    filename: str,
) -> dict[str, Any]:
    facts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    patterns = (
        ("date", _DATE_PATTERN),
        ("time", _TIME_PATTERN),
        ("amount", _AMOUNT_PATTERN),
        ("url", _URL_PATTERN),
        ("email", _EMAIL_PATTERN),
        ("transaction_reference", _TRANSACTION_PATTERN),
    )
    for sentence in _sentences(text):
        for fact_type, pattern in patterns:
            for match in pattern.finditer(sentence):
                value = match.group(1) if fact_type == "transaction_reference" else match.group(0)
                key = (fact_type, value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    {
                        "type": fact_type,
                        "value": value,
                        "source_excerpt": _source_excerpt(sentence),
                        "evidence_id": evidence_id,
                        "evidence_filename": filename,
                        "review_status": "candidate",
                    }
                )
    return {
        "facts": facts,
        "method": "local_pattern_matching",
        "notice": "Candidates are copied from extracted evidence text and are not independently verified.",
    }


def _parse_unambiguous_date(value: str) -> str | None:
    formats = (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
    )
    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", value)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            month, day = first, second
        else:
            return None
        if year < 100:
            year += 2000 if year < 70 else 1900
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None


def _parse_unambiguous_time(value: str) -> str | None:
    normalized = value.strip().upper()
    for time_format in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(normalized, time_format).time().isoformat(timespec="minutes")
        except ValueError:
            continue
    return None


def generate_timeline_candidates(
    evidence_records: list[Any],
    incident: Any,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if incident.summary and incident.incident_date:
        events.append(
            {
                "date": incident.incident_date.isoformat(),
                "time": incident.incident_time.isoformat(timespec="minutes") if incident.incident_time else None,
                "description": incident.summary,
                "source": "Incident form",
                "evidence_id": None,
                "evidence_filename": None,
                "origin": "incident",
                "review_status": "reported",
            }
        )

    for evidence in evidence_records:
        if evidence.extraction_status not in {"extracted", "ocr_extracted"} or not evidence.extracted_text:
            continue
        text = evidence.extracted_text
        timestamp_matches = list(_CONVERSATION_TIME_PATTERN.finditer(text))
        explicit_dates = [
            (match.start(), match.group(0), _parse_unambiguous_date(match.group(0)))
            for match in _DATE_PATTERN.finditer(text)
        ]
        confirmed_incident_date = (
            incident.incident_date.isoformat()
            if incident.incident_date
            else None
        )
        for index, timestamp in enumerate(timestamp_matches):
            message_end = timestamp_matches[index + 1].start() if index + 1 < len(timestamp_matches) else len(text)
            message_text = text[timestamp.end():message_end]
            message_text = re.sub(r"^[\s:：,\.;\-–—|•\[\]()]+", "", message_text)
            message_text = re.sub(r"[\s\r\n]+", " ", message_text).strip()
            if not message_text:
                continue

            date_in_message = next(
                (entry for entry in explicit_dates if timestamp.end() <= entry[0] < message_end),
                None,
            )
            date_before_conversation = next(
                (
                    entry for entry in explicit_dates
                    if entry[0] < timestamp.start()
                    and (index == 0 or entry[0] > timestamp_matches[index - 1].start())
                ),
                None,
            )
            date_reference = date_in_message or date_before_conversation
            if date_reference:
                normalized_date, date_text = date_reference[2], date_reference[1]
            elif len(explicit_dates) == 1:
                normalized_date, date_text = explicit_dates[0][2], explicit_dates[0][1]
            elif not explicit_dates and confirmed_incident_date:
                normalized_date, date_text = confirmed_incident_date, None
            else:
                normalized_date, date_text = None, None

            events.append(
                {
                    "date": normalized_date,
                    "date_text": date_text,
                    "time": _parse_unambiguous_time(timestamp.group(0).replace(".", "")),
                    "description": _source_excerpt(timestamp.group(0) + " — " + message_text, 2000),
                    "source": evidence.original_filename,
                    "evidence_id": evidence.id,
                    "evidence_filename": evidence.original_filename,
                    "origin": "evidence",
                    "review_status": "candidate",
                }
            )

        for sentence in _sentences(text):
            if _CONVERSATION_TIME_PATTERN.search(sentence):
                continue
            date_match = _DATE_PATTERN.search(sentence)
            if not date_match:
                continue
            normalized_date = _parse_unambiguous_date(date_match.group(0))
            time_match = _TIME_PATTERN.search(sentence)
            events.append(
                {
                    "date": normalized_date,
                    "date_text": date_match.group(0),
                    "time": _parse_unambiguous_time(time_match.group(0)) if time_match else None,
                    "description": _source_excerpt(sentence, 2000),
                    "source": evidence.original_filename,
                    "evidence_id": evidence.id,
                    "evidence_filename": evidence.original_filename,
                    "origin": "evidence",
                    "review_status": "candidate",
                }
            )

    unique: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for event in events:
        key = (
            event.get("evidence_id") or "incident",
            event.get("date_text") or event.get("date"),
            event.get("time"),
            event["description"],
        )
        unique[key] = event
    return sorted(
        unique.values(),
        key=lambda item: (item.get("date") or "9999-99-99", item.get("time") or ""),
    )


def detect_missing_information(incident: Any, evidence_records: list[Any]) -> list[dict[str, str]]:
    prompts = [
        ("Incident type", incident.incident_type, "Select a type if it is known."),
        ("Incident date", incident.incident_date, "Add the date or mark it approximate if known."),
        ("Incident time", incident.incident_time, "Add the time if it is known."),
        ("Incident description", incident.summary, "Describe what happened in your own words."),
        ("Platform or service", incident.platform, "Name the app, bank, website, or service if known."),
        ("Reported contact or profile", incident.reported_identifier, "Add the URL, phone, email, or profile if relevant."),
        ("Incident location", incident.location, "Add the relevant city, state, or location if known."),
    ]
    financial_types = {
        "online financial fraud",
        "online shopping fraud",
        "phishing",
        "identity theft",
        "account compromise",
    }
    if (
        (incident.incident_type or "").casefold() in financial_types
        or incident.loss_amount
        or incident.transaction_reference
    ):
        prompts.extend(
            (
                ("Financial loss", incident.loss_amount, "Record the amount if money was lost and the amount is known."),
                ("Transaction reference", incident.transaction_reference, "Add a UTR, transaction ID, or order number if available."),
            )
        )
    prompts.append(
        ("Supporting evidence", evidence_records, "List relevant records if available; do not create or alter evidence.")
    )
    complainant = (incident.workflow_data or {}).get("complainant", {})
    complainant_recorded = isinstance(complainant, dict) and any(
        value is not None and str(value).strip()
        for value in (complainant.get("name"), complainant.get("phone"), complainant.get("email"), complainant.get("address"))
    )
    prompts.append(
        (
            "Complainant details",
            complainant_recorded,
            "Add complainant details if they are available and required by the reporting channel.",
        )
    )
    return [
        {
            "field": label,
            "status": "recorded" if _has_recorded_value(value) else "needs_review",
            "prompt": prompt,
        }
        for label, value, prompt in prompts
    ]


def build_complaint_draft(
    incident: Any,
    evidence_records: list[Any],
    timeline_events: list[dict[str, Any]],
    missing_information: list[dict[str, str]],
    narrative_override: str | None = None,
) -> str:
    lines = [
        "DRAFT COMPLAINT - REVIEW BEFORE SUBMISSION",
        "Prepared from information recorded for this incident. Review all details against original records before submission.",
    ]
    complainant = (incident.workflow_data or {}).get("complainant", {})
    complainant_fields = (
        ("Name", complainant.get("name")),
        ("Phone", complainant.get("phone")),
        ("Email", complainant.get("email")),
        ("Address", complainant.get("address")),
    ) if isinstance(complainant, dict) else ()
    recorded_complainant = [
        (label, " ".join(str(value).split()))
        for label, value in complainant_fields
        if value is not None and str(value).strip()
    ]
    if recorded_complainant:
        lines.extend(("", "COMPLAINANT DETAILS"))
        lines.extend(f"{label}: {value}" for label, value in recorded_complainant)
    else:
        lines.extend(("", "COMPLAINANT DETAILS", "Not provided"))

    subject = f"Report of {incident.incident_type}" if incident.incident_type else "Cybercrime incident report"
    lines.extend(("", f"Subject: {subject}", "", "INCIDENT NARRATIVE"))
    if incident.summary and str(incident.summary).strip():
        lines.extend(
            (
                "I wish to report the following incident.",
                "Complainant's account as recorded (not independently verified):",
                str(incident.summary).strip(),
            )
        )
    else:
        lines.append("Complainant's account: Not provided")
    if narrative_override and narrative_override.strip():
        lines.extend(
            (
                "",
                "AI-GENERATED WORDING SUGGESTION - UNVERIFIED",
                "This is not the complainant's statement. Check it against the recorded account and evidence before using any wording.",
                narrative_override.strip(),
            )
        )

    lines.extend(("", "RECORDED INCIDENT DETAILS"))
    recorded_fields = (
        ("Incident type", incident.incident_type),
        ("Date", incident.incident_date),
        ("Time", incident.incident_time),
        ("Platform or service", incident.platform),
        ("Reported contact or profile", incident.reported_identifier),
        ("Transaction reference", incident.transaction_reference),
        ("Reported financial loss", incident.loss_amount),
        ("Location", incident.location),
        ("Additional request", incident.requested_action),
    )
    for label, value in recorded_fields:
        if value is not None and str(value).strip():
            if label == "Time" and hasattr(value, "strftime"):
                value = value.strftime("%H:%M")
            elif label == "Date" and hasattr(value, "isoformat"):
                value = value.isoformat()
            if label == "Reported financial loss":
                value = _format_reported_loss(value)
        else:
            value = "Not provided"
        lines.append(f"- {label}: {value}")

    confirmed_facts = [
        (evidence, fact)
        for evidence in evidence_records
        for fact in (evidence.structured_data or {}).get("facts", [])
        if fact.get("review_status") == "confirmed" and _has_recorded_value(fact.get("value"))
    ]
    if confirmed_facts:
        lines.extend(("", "USER-CONFIRMED EVIDENCE FACTS"))
        for evidence, fact in confirmed_facts:
            excerpt = " ".join(str(fact.get("source_excerpt") or "").split())
            description = f"- {fact.get('type') or 'Fact'}: {fact['value']}"
            if excerpt:
                description += f" [Source: {evidence.original_filename}; excerpt: {excerpt}]"
            lines.append(description)

    events = list(timeline_events)
    if incident.summary and str(incident.summary).strip() and not any(
        event.get("origin") == "incident" for event in events
    ):
        events.append(
            {
                "date": incident.incident_date.isoformat() if incident.incident_date else None,
                "time": incident.incident_time.strftime("%H:%M") if incident.incident_time else None,
                "description": str(incident.summary).strip(),
                "source": "Incident Form",
                "origin": "incident",
                "review_status": "reported",
            }
        )
    evidence_by_id = {evidence.id: evidence for evidence in evidence_records}
    ordered_events = sorted(events, key=_timeline_sort_key)
    ordered_events = [
        event for event in ordered_events
        if event.get("description") is not None and str(event["description"]).strip()
    ]
    lines.extend(("", "CHRONOLOGICAL TIMELINE"))
    if ordered_events:
        lines.extend(("| Date & Time | Event Description | Source |", "| --- | --- | --- |"))
        for event in ordered_events:
            evidence = evidence_by_id.get(event.get("evidence_id"))
            date_value = event.get("date") or event.get("date_text") or "Not provided"
            time_value = event.get("time")
            when = " ".join(filter(None, (str(date_value), str(time_value) if time_value else "")))
            needs_verification = (
                event.get("origin") == "evidence"
                and event.get("review_status") != "reviewed"
            )
            if event.get("origin") == "incident":
                source = "Incident Form"
            elif event.get("origin") == "evidence":
                source = (
                    (evidence.original_filename if evidence else None)
                    or event.get("evidence_filename")
                    or event.get("source")
                    or "Evidence source not provided"
                )
                if needs_verification:
                    if evidence and evidence.extraction_status == "ocr_extracted":
                        source += " - OCR extracted candidate; verify against the original"
                    else:
                        source += " - extracted candidate; verify against the original"
            else:
                source = event.get("source") or "User-provided timeline entry"
            description = " ".join(str(event.get("description") or "").split())
            if needs_verification:
                description = "Unverified extracted candidate: " + description
            cells = (when, description, source)
            lines.append("| " + " | ".join(_complaint_table_cell(value) for value in cells) + " |")
    else:
        lines.append("No timeline events have been provided for this incident.")

    lines.extend(("", "EVIDENCE PROVIDED"))
    if evidence_records:
        lines.extend(("", "| Evidence File | Evidence Type | Description / Reference |", "| --- | --- | --- |"))
        for evidence in evidence_records:
            evidence_type = evidence.file_type or "Not provided"
            description = evidence.category or ""
            if evidence.id:
                description = f"{description}; Evidence ID: {evidence.id}" if description else f"Evidence ID: {evidence.id}"
            cells = (
                evidence.original_filename or "Not provided",
                evidence_type,
                description or "Not provided",
            )
            lines.append("| " + " | ".join(_complaint_table_cell(value) for value in cells) + " |")
    else:
        lines.append("No evidence has been provided for this incident.")

    outstanding = [
        (item["field"], item["prompt"])
        for item in missing_information
        if item["status"] != "recorded"
    ]
    if not any(field == "Complainant details" for field, _ in outstanding) and not recorded_complainant:
        outstanding.append(
            ("Complainant details", "Add your name and contact details if required by the reporting channel.")
        )
    if not ordered_events:
        outstanding.append(("Timeline", "Add dated events if known."))
    lines.extend(("", "MISSING-INFORMATION CHECKLIST"))
    if outstanding:
        lines.extend(f"- {field}: {prompt}" for field, prompt in outstanding)
    else:
        lines.append("No missing incident fields were identified by the current checklist.")
    lines.extend(
        (
            "",
            "CLOSING",
            "Please review this draft against the original records, correct any inaccuracies, and provide any missing information required by the reporting channel before submission.",
            "",
            "Complainant signature: ______________________________",
            "Date: ______________________________",
        )
    )
    return "\n".join(lines)


def _complaint_table_cell(value: Any) -> str:
    return (
        " ".join(str(value or "").split())
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
    )


def _format_reported_loss(value: Any) -> str:
    text = " ".join(str(value).split())
    numeric = re.fullmatch(r"(?:(?:INR|Rs\.?|₹)\s*)?([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if numeric:
        amount = numeric.group(1).replace(",", "")
        whole, separator, fraction = amount.partition(".")
        formatted = f"{int(whole):,}" + (separator + fraction if separator else "")
        return f"₹{formatted}"
    if re.match(r"^(?:INR|Rs\.?)\s*", text, re.IGNORECASE):
        return re.sub(r"^(?:INR|Rs\.?)\s*", "₹", text, flags=re.IGNORECASE)
    return text


def _has_recorded_value(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _timeline_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    date_value = str(event.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        date_value = "9999-99-99"
    time_value = str(event.get("time") or "")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?", time_value):
        time_value = "99:99"
    return date_value, time_value


def render_complaint_pdf(complaint_text: str) -> bytes:
    import pymupdf as fitz

    page_width, page_height = fitz.paper_size("a4")
    margin = 48
    content_width = page_width - 2 * margin
    bottom = page_height - 48
    regular_font_file, bold_font_file = _complaint_font_files()
    body_font = "CA-Body"
    bold_font = "CA-Bold"
    body_metrics = fitz.Font(fontfile=regular_font_file) if regular_font_file else fitz.Font("helv")
    bold_metrics = fitz.Font(fontfile=bold_font_file) if bold_font_file else fitz.Font("hebo")
    document = fitz.open()
    page = None
    y = 0.0

    def new_page() -> None:
        nonlocal page, y
        page = document.new_page(width=page_width, height=page_height)
        y = 48
        page.insert_text(
            (margin, 30), "CYBER ASSIST | COMPLAINT DRAFT", fontname=bold_font,
            fontfile=bold_font_file, fontsize=8, color=(0.10, 0.20, 0.34),
        )
        page.draw_line((margin, 38), (page_width - margin, 38), color=(0.10, 0.20, 0.34), width=0.7)

    def ensure_space(height: float) -> None:
        if page is None or y + height > bottom:
            new_page()

    def wrapped_lines(text: str, width: float, font_metrics: Any, size: float) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        result = []
        current = ""
        for word in words:
            fragments = [word]
            if font_metrics.text_length(word, fontsize=size) > width:
                fragments = []
                fragment = ""
                for character in word:
                    candidate = fragment + character
                    if fragment and font_metrics.text_length(candidate, fontsize=size) > width:
                        fragments.append(fragment)
                        fragment = character
                    else:
                        fragment = candidate
                if fragment:
                    fragments.append(fragment)
            for fragment in fragments:
                candidate = f"{current} {fragment}".strip()
                if current and font_metrics.text_length(candidate, fontsize=size) > width:
                    result.append(current)
                    current = fragment
                else:
                    current = candidate
                if fragment != fragments[-1]:
                    result.append(current)
                    current = ""
        result.append(current)
        return result

    def draw_paragraph(text: str, font: str = body_font, size: float = 10, color=(0.15, 0.18, 0.22), indent: float = 0) -> None:
        nonlocal y
        width = content_width - indent
        line_height = size * 1.45
        metrics = bold_metrics if font == bold_font else body_metrics
        font_file = bold_font_file if font == bold_font else regular_font_file
        for line in wrapped_lines(text, width, metrics, size):
            ensure_space(line_height + 2)
            page.insert_text(
                (margin + indent, y + size), line, fontname=font, fontfile=font_file,
                fontsize=size, color=color,
            )
            y += line_height
        y += 3

    def parse_table_row(line: str) -> list[str]:
        content = line.strip()
        if content.startswith("|"):
            content = content[1:]
        if content.endswith("|") and not content.endswith("\\|"):
            content = content[:-1]
        cells = []
        current = []
        escaped = False
        for character in content:
            if character == "|" and not escaped:
                cells.append("".join(current).strip())
                current = []
            else:
                current.append(character)
            escaped = character == "\\" and not escaped
            if character != "\\":
                escaped = False
        cells.append("".join(current).strip())
        return [cell.replace("\\|", "|").replace("\\\\", "\\") for cell in cells]

    def draw_table(rows: list[list[str]]) -> None:
        nonlocal y
        if not rows:
            return
        column_count = max(len(row) for row in rows)
        header_labels = [cell.casefold() for cell in rows[0]]
        if header_labels == ["date & time", "event description", "source"]:
            proportions = [0.23, 0.50, 0.27]
        elif header_labels == ["evidence file", "evidence type", "description / reference"]:
            proportions = [0.32, 0.20, 0.48]
        else:
            proportions = [1 / column_count] * column_count
        widths = [content_width * part for part in proportions]
        header = rows[0]
        data_rows = rows[1:]
        if data_rows and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in data_rows[0]):
            data_rows = data_rows[1:]

        def draw_row(cells: list[str], is_header: bool) -> None:
            nonlocal y
            cells = cells + [""] * (column_count - len(cells))
            font = bold_font if is_header else body_font
            metrics = bold_metrics if is_header else body_metrics
            font_file = bold_font_file if is_header else regular_font_file
            size = 8.5 if is_header else 8
            padding = 6
            all_cell_lines = [
                wrapped_lines(cell, width - padding * 2, metrics, size)
                for cell, width in zip(cells, widths)
            ]
            line_height = size * 1.55
            remaining = [list(lines) for lines in all_cell_lines]
            while any(remaining):
                max_lines = max(1, int((bottom - y - padding * 2) / line_height))
                if y + min(24, max_lines * line_height + padding * 2) > bottom:
                    new_page()
                    if not is_header:
                        draw_row(header, True)
                    max_lines = max(1, int((bottom - y - padding * 2) / line_height))
                fragment = [cell[:max_lines] for cell in remaining]
                remaining = [cell[max_lines:] for cell in remaining]
                row_height = max(24, max(len(cell) for cell in fragment) * line_height + padding * 2 + 3)
                for index, (wrapped, width) in enumerate(zip(fragment, widths)):
                    x = margin + sum(widths[:index])
                    rectangle = fitz.Rect(x, y, x + width, y + row_height)
                    page.draw_rect(
                        rectangle,
                        color=(0.78, 0.82, 0.88),
                        fill=(0.10, 0.20, 0.34) if is_header else (1, 1, 1),
                        width=0.5,
                    )
                    page.insert_textbox(
                        fitz.Rect(x + padding, y + padding - 1, x + width - padding, y + row_height - padding),
                        "\n".join(wrapped),
                        fontname=font,
                        fontfile=font_file,
                        fontsize=size,
                        color=(1, 1, 1) if is_header else (0.12, 0.18, 0.25),
                        lineheight=1.4,
                    )
                y += row_height
                if any(remaining):
                    new_page()
                    if not is_header:
                        draw_row(header, True)

        ensure_space(28)
        draw_row(header, True)
        for row in data_rows:
            draw_row(row, False)
        y += 10

    lines = complaint_text.splitlines()
    if not any(line.strip() for line in lines):
        document.close()
        raise ValueError("Complaint text is empty.")
    new_page()
    page.insert_text(
        (margin, y + 15), lines[0].strip(), fontname=bold_font, fontfile=bold_font_file,
        fontsize=14, color=(0.10, 0.20, 0.34),
    )
    y += 28
    index = 1
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            y += 5
            index += 1
            continue
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(parse_table_row(lines[index]))
                index += 1
            draw_table(table_lines)
            continue
        if line.isupper() and len(line) < 70:
            ensure_space(34)
            y += 8
            page.insert_text(
                (margin, y + 11), line, fontname=bold_font, fontfile=bold_font_file,
                fontsize=10.5, color=(0.10, 0.31, 0.49),
            )
            y += 20
            index += 1
            continue
        if line.startswith("Subject:"):
            draw_paragraph(line, font=bold_font, size=10.5, color=(0.12, 0.20, 0.30))
        elif line.startswith("- "):
            draw_paragraph(line[2:], indent=12)
        else:
            draw_paragraph(line)
        index += 1

    for page_index in range(document.page_count):
        current_page = document[page_index]
        current_page.draw_line((margin, page_height - 34), (page_width - margin, page_height - 34), color=(0.82, 0.85, 0.89), width=0.6)
        current_page.insert_text(
            (page_width - margin - 60, page_height - 20),
            f"Page {page_index + 1} of {document.page_count}",
            fontname=body_font,
            fontfile=regular_font_file,
            fontsize=8,
            color=(0.40, 0.44, 0.49),
        )
    result = document.tobytes()
    document.close()
    return result


def _complaint_font_files() -> tuple[str | None, str | None]:
    candidates = (
        (
            Path(r"C:\Windows\Fonts\Nirmala.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ),
        (
            Path(r"C:\Windows\Fonts\NirmalaB.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        ),
    )
    selected = []
    for paths in candidates:
        selected.append(next((str(path) for path in paths if path.is_file()), None))
    return selected[0], selected[1] or selected[0]


_GUIDANCE = (
    {
        "id": "preserve-originals",
        "title": "Preserve original digital evidence",
        "text": "Keep original files unchanged, preserve relevant message context and available timestamps, and note where a copy came from. Work from copies when reviewing or annotating.",
        "source": "Cyber Assist local evidence-preservation checklist",
    },
    {
        "id": "contact-provider",
        "title": "Contact the relevant financial provider",
        "text": "If a payment or account is involved, contact the relevant bank or payment provider using its official contact channel and ask what protective steps or dispute options are available. Record any reference number they provide.",
        "source": "Cyber Assist local incident-preparation checklist",
    },
    {
        "id": "secure-accounts",
        "title": "Secure affected accounts",
        "text": "Use the service's official recovery and security controls for an affected account. Change compromised credentials from a trusted device and enable available multi-factor protection. Never share passwords, PINs, or one-time codes.",
        "source": "Cyber Assist local account-safety checklist",
    },
    {
        "id": "reporting-channel",
        "title": "Use an appropriate official reporting channel",
        "text": "Check the reporting authority's current official instructions for your location and incident type. Keep a copy of the submitted report and any acknowledgement. This local checklist does not determine jurisdiction or provide legal advice.",
        "source": "Cyber Assist local reporting checklist",
    },
    {
        "id": "draft-review",
        "title": "Review the complaint before sending",
        "text": "Check every name, date, amount, reference, and statement against original records. Distinguish what you observed from what you infer, and keep uncertain details marked for review.",
        "source": "Cyber Assist local complaint-review checklist",
    },
)


def retrieve_guidance(query: str, limit: int = 5) -> list[dict[str, Any]]:
    terms = {
        token.casefold()
        for token in re.findall(r"[a-z0-9]+", query)
        if len(token) > 2 and token.casefold() not in _STOP_WORDS
    }
    if not terms:
        return []
    results = []
    for document in _GUIDANCE:
        document_terms = set(
            token.casefold()
            for token in re.findall(r"[a-z0-9]+", document["title"] + " " + document["text"])
        )
        overlap = terms & document_terms
        if overlap:
            results.append(
                {
                    **document,
                    "matched_terms": sorted(overlap),
                    "score": round(len(overlap) / max(len(terms), 1), 3),
                }
            )
    return sorted(results, key=lambda item: (-item["score"], item["id"]))[:limit]
