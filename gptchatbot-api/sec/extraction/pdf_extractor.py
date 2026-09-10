import os
import json
import uuid
import fitz
import numpy as np

from celery import shared_task
from django.db import connections, transaction
from openai import OpenAI

from rapidocr_onnxruntime import RapidOCR
import tempfile

MIN_TEXT_LENGTH = 30

OPENAI_MODEL = os.getenv(
    "SEC_EXTRACTION_MODEL",
    "gpt-5.6-luna",
)

EMBEDDING_MODEL = os.getenv(
    "SEC_EMBEDDING_MODEL",
    "text-embedding-3-small",
)

openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    max_retries=0,
    timeout=120.0,
)


ocr = RapidOCR()
def ocr_image(img):
    result, _ = ocr(img)

    if not result:
        return ""

    return "\n".join(
        line[1] for line in result
    )

def extract_pdf_text(file_bytes):
    """
    Extract general document text.

    Uses native PDF text when available.
    Falls back to OCR for pages with little/no text.
    """

    pdf = fitz.open(
        stream=file_bytes,
        filetype="pdf",
    )

    pages = []

    try:
        for page_number, page in enumerate(pdf, start=1):

            text = (
                page.get_text("text") or ""
            ).strip()

            extraction_method = "text"

            # OCR only when the PDF does not have
            # enough usable text.
            if len(text) < MIN_TEXT_LENGTH:

                pix = page.get_pixmap(
                    matrix=fitz.Matrix(1.5, 1.5),
                    colorspace=fitz.csRGB,
                    alpha=False,
                )

                img = np.frombuffer(
                    pix.samples,
                    dtype=np.uint8,
                ).reshape(
                    pix.height,
                    pix.width,
                    3,
                )

                text = (
                    ocr_image(img) or ""
                ).strip()

                extraction_method = "ocr"

            if text:
                pages.append({
                    "page_number": page_number,
                    "text": text,
                    "extraction_method": extraction_method,
                })

    finally:
        pdf.close()

    return pages


def chunk_text(text, max_chars=6000):
    """
    Simple character-based chunking.

    Keeps the implementation lightweight.
    """

    text = text.strip()

    if not text:
        return []

    chunks = []

    start = 0
    text_length = len(text)

    while start < text_length:

        end = min(
            start + max_chars,
            text_length,
        )

        # Try to end at a paragraph/newline.
        if end < text_length:
            newline_pos = text.rfind(
                "\n",
                start,
                end,
            )

            if newline_pos > start + 1000:
                end = newline_pos

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start = end

    return chunks


def create_embeddings(texts):
    """
    Generate embeddings in one API call.
    """

    if not texts:
        return []

    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )

    # Keep original ordering.
    embeddings = sorted(
        response.data,
        key=lambda item: item.index,
    )

    return [
        item.embedding
        for item in embeddings
    ]


def extract_tables_from_pdf(
    file_path,
    source_page_numbers,
):
    """
    Send only table-containing pages to the multimodal LLM.

    source_page_numbers contains the ORIGINAL PDF page numbers
    corresponding to the pages inside file_path.
    """

    print(
        "SEC TABLE EXTRACTION: "
        f"sending {len(source_page_numbers)} "
        "selected pages to gpt-5.6-terra..."
    )

    print(
        "SEC TABLE EXTRACTION: "
        f"original pages: {source_page_numbers}"
    )

    with open(file_path, "rb") as pdf_file:

        uploaded_file = openai_client.files.create(
            file=pdf_file,
            purpose="user_data",
        )

    try:

        page_mapping = "\n".join(
            f"Uploaded PDF page {index} = "
            f"original PDF page {original_page}"
            for index, original_page in enumerate(
                source_page_numbers,
                start=1,
            )
        )

        prompt = f"""
You are extracting tables from an SEC filing PDF.

The uploaded PDF contains ONLY selected pages from the
original SEC filing.

IMPORTANT PAGE MAPPING:

{page_mapping}

When reporting page_start and page_end, use the
ORIGINAL PDF PAGE NUMBERS, NOT the uploaded PDF page numbers.

Analyze the ORIGINAL VISUAL CONTENT of the uploaded pages.

Your job is to extract ACTUAL TABLES from the document.

IMPORTANT:

1. Extract every meaningful table in the uploaded pages.
2. Tables may be borderless.
3. Use visual positioning and surrounding headings to determine
   rows and columns.
4. Do NOT rely only on the PDF's flattened text order.
5. Preserve the exact values appearing in the document.
6. Do not invent, calculate, normalize, or infer missing values.
7. Preserve percentages, numbers, dates, labels, and totals exactly
   as they appear.
8. If multiple tables appear on one page, return them separately.
9. If a table spans multiple uploaded pages, treat it as one table
   when appropriate.
10. Use the ORIGINAL PDF page numbers from the page mapping above.
11. Do not treat ordinary paragraphs, signatures, addresses,
    form labels, or narrative text as tables.
12. Do not omit rows merely because they contain blank cells.
13. Preserve the logical column structure of the table.
14. Return an empty `tables` array if there are no tables.

For each table return:

- table_index
- table_title
- page_start
- page_end
- columns
- rows
- raw_table

`columns` must contain the column names in their logical order.

`rows` must contain one object per row, using the column names
as keys.

`raw_table` should be a readable plain-text representation
of the table.

Do not include commentary outside the JSON structure.

Return ONLY valid JSON in this exact structure:

{{
    "tables": []
}}
"""

        print(
            "SEC TABLE EXTRACTION: "
            "calling gpt-5.6-terra..."
        )

        response = openai_client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_id": uploaded_file.id,
                        },
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        print(
            "SEC TABLE EXTRACTION: "
            "LLM response received."
        )

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage:
            print(
                "SEC TABLE EXTRACTION USAGE: "
                f"input_tokens={usage.input_tokens}, "
                f"output_tokens={usage.output_tokens}, "
                f"total_tokens={usage.total_tokens}"
            )

        output_text = (
            response.output_text or ""
        ).strip()

        print(
            "SEC TABLE EXTRACTION: "
            f"output length: {len(output_text)}"
        )

        if output_text.startswith("```"):

            output_text = (
                output_text
                .replace(
                    "```json",
                    "",
                    1,
                )
                .replace(
                    "```",
                    "",
                )
                .strip()
            )

        result = json.loads(
            output_text
        )

        tables = result.get(
            "tables",
            [],
        )

        print(
            "SEC TABLE EXTRACTION: "
            f"extracted {len(tables)} tables."
        )

        return tables

    finally:

        try:
            openai_client.files.delete(
                uploaded_file.id
            )

        except Exception as exc:

            print(
                "SEC TABLE EXTRACTION: "
                f"failed to delete uploaded file: {exc}"
            )
            
def looks_like_table_text(text: str) -> bool:
    """
    Conservative table detector.

    This does NOT need to perfectly identify tables.
    It only needs to determine whether a page is worth
    sending to the multimodal LLM.

    Designed to favor recall over precision.
    """

    if not text:
        return False

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if len(lines) < 5:
        return False

    text_lower = text.lower()

    # ---------------------------------------------------------
    # 1. Numeric-heavy lines
    # ---------------------------------------------------------

    numeric_lines = 0

    for line in lines:
        digit_count = sum(
            char.isdigit()
            for char in line
        )

        if digit_count >= 2:
            numeric_lines += 1

    # ---------------------------------------------------------
    # 2. Short / structured lines
    # ---------------------------------------------------------

    short_lines = sum(
        1
        for line in lines
        if len(line) <= 100
    )

    # ---------------------------------------------------------
    # 3. Common SEC table terminology
    # ---------------------------------------------------------

    table_keywords = [
        "total",
        "shares",
        "amount",
        "percentage",
        "%",
        "year ended",
        "balance",
        "assets",
        "liabilities",
        "equity",
        "income",
        "revenue",
        "cost",
        "expense",
        "fair value",
        "ownership",
        "capital",
        "outstanding",
        "issued",
        "weighted average",
    ]

    keyword_hits = sum(
        1
        for keyword in table_keywords
        if keyword in text_lower
    )

    # ---------------------------------------------------------
    # 4. Table-like separators
    # ---------------------------------------------------------

    separator_lines = sum(
        1
        for line in lines
        if (
            "|" in line
            or "\t" in line
            or "  " in line
        )
    )

    # ---------------------------------------------------------
    # Scoring
    # ---------------------------------------------------------

    score = 0

    if numeric_lines >= 3:
        score += 2

    elif numeric_lines >= 2:
        score += 1

    if short_lines >= 5:
        score += 1

    if keyword_hits >= 2:
        score += 1

    if separator_lines >= 2:
        score += 1

    return score >= 3

def looks_like_native_table(page) -> bool:
    """
    Detect table-like structure using native PDF word positions.

    We are NOT reconstructing the table here.
    We only determine whether the page is worth sending
    to the multimodal LLM.
    """

    words = page.get_text(
        "words",
        sort=True,
    )

    if not words:
        return False

    # ---------------------------------------------------------
    # Group words into approximate lines
    # ---------------------------------------------------------

    lines = {}

    for word in words:
        x0, y0, x1, y1, text = word[:5]

        # Quantize Y position so words on the same line
        # are grouped together.
        line_key = round(y0 / 3) * 3

        lines.setdefault(
            line_key,
            [],
        ).append({
            "x0": float(x0),
            "x1": float(x1),
            "text": text,
        })

    if len(lines) < 4:
        return False

    # ---------------------------------------------------------
    # Look for lines containing multiple separated columns
    # ---------------------------------------------------------

    structured_lines = 0
    numeric_lines = 0

    for line_words in lines.values():

        if len(line_words) < 2:
            continue

        line_words.sort(
            key=lambda item: item["x0"]
        )

        column_gaps = 0

        for previous, current in zip(
            line_words,
            line_words[1:],
        ):
            gap = current["x0"] - previous["x1"]

            if gap > 25:
                column_gaps += 1

        if column_gaps >= 1:
            structured_lines += 1

        line_text = " ".join(
            item["text"]
            for item in line_words
        )

        digit_count = sum(
            char.isdigit()
            for char in line_text
        )

        if digit_count >= 2:
            numeric_lines += 1

    # ---------------------------------------------------------
    # Strong table signal
    # ---------------------------------------------------------

    if structured_lines >= 4 and numeric_lines >= 2:
        return True

    return False

def extract_ocr_text_for_detection(page):
    """
    Render a scanned page and OCR it.

    Used only for determining whether the page
    potentially contains a table.
    """

    pix = page.get_pixmap(
        matrix=fitz.Matrix(
            1.5,
            1.5,
        ),
        colorspace=fitz.csRGB,
        alpha=False,
    )

    img = np.frombuffer(
        pix.samples,
        dtype=np.uint8,
    ).reshape(
        pix.height,
        pix.width,
        3,
    )

    return (
        ocr_image(img) or ""
    ).strip()
    
    
def detect_table_pages(file_bytes):
    """
    Detect pages that potentially contain tables.

    Supports:

    - native PDFs
    - scanned PDFs

    Returns 1-based original PDF page numbers.
    """

    pdf = fitz.open(
        stream=file_bytes,
        filetype="pdf",
    )

    detected_pages = []

    try:

        total_pages = len(pdf)

        for page_index, page in enumerate(
            pdf,
            start=1,
        ):

            print(
                f"SEC TABLE DETECTION: "
                f"checking page {page_index}/{total_pages}"
            )

            native_text = (
                page.get_text("text") or ""
            ).strip()

            # -------------------------------------------------
            # Native PDF
            # -------------------------------------------------

            if len(native_text) >= MIN_TEXT_LENGTH:

                text_signal = (
                    looks_like_table_text(
                        native_text
                    )
                )

                geometry_signal = (
                    looks_like_native_table(
                        page
                    )
                )

                if (
                    text_signal
                    or geometry_signal
                ):
                    detected_pages.append(
                        page_index
                    )

                    print(
                        f"SEC TABLE DETECTION: "
                        f"page {page_index} "
                        f"LIKELY TABLE"
                    )

                continue

            # -------------------------------------------------
            # Scanned PDF
            # -------------------------------------------------

            print(
                f"SEC TABLE DETECTION: "
                f"page {page_index} "
                f"appears scanned; running OCR"
            )

            ocr_text = extract_ocr_text_for_detection(
                page
            )

            if looks_like_table_text(
                ocr_text
            ):
                detected_pages.append(
                    page_index
                )

                print(
                    f"SEC TABLE DETECTION: "
                    f"page {page_index} "
                    f"LIKELY TABLE (OCR)"
                )

    finally:
        pdf.close()

    return detected_pages

def expand_table_pages(
    pages,
    total_pages,
    radius=1,
):
    """
    Expand detected table pages so that nearby pages
    containing table headers/continuations are preserved.
    """

    expanded = set()

    for page in pages:

        for offset in range(
            -radius,
            radius + 1,
        ):
            candidate = page + offset

            if (
                1 <= candidate <= total_pages
            ):
                expanded.add(candidate)

    return sorted(expanded)

def create_table_pages_pdf(
    file_bytes,
    page_numbers,
):
    """
    Create a temporary PDF containing only the selected pages.

    page_numbers are 1-based original PDF page numbers.

    Returns:

        temp_file_path
    """

    source_pdf = fitz.open(
        stream=file_bytes,
        filetype="pdf",
    )

    output_pdf = fitz.open()

    try:

        for page_number in page_numbers:

            source_index = page_number - 1

            output_pdf.insert_pdf(
                source_pdf,
                from_page=source_index,
                to_page=source_index,
            )

        temp_file = tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
        )

        temp_file_path = temp_file.name

        temp_file.close()

        output_pdf.save(
            temp_file_path,
        )

        return temp_file_path

    finally:
        output_pdf.close()
        source_pdf.close()