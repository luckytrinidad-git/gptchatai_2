import json
import os
import re
from typing import Any, Dict, List

import fitz
import pdfplumber
from openai import OpenAI
from gptchatbot.settings import OPENAI_API_KEY

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================
# TABLE EXTRACTION SCHEMA
# ============================================================
GIS_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_number": {
            "type": "integer"
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string"
                    },
                    "type": {
                        "type": "string",
                        "enum": [
                            "text",
                            "table",
                            "key_value",
                            "list",
                            "mixed"
                        ]
                    },
                    "content": {
                        "type": "string"
                    },
                    "data": {
                        "type": "string"
                    }
                },
                "required": [
                    "title",
                    "type",
                    "content",
                    "data"
                ],
                "additionalProperties": False
            }
        }
    },
    "required": [
        "page_number",
        "sections"
    ],
    "additionalProperties": False
}
TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "table_title": {
                        "type": "string"
                    },
                    "page_start": {
                        "type": "integer"
                    },
                    "page_end": {
                        "type": "integer"
                    },
                    "columns": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "string"
                            }
                        }
                    },
                    "unit": {
                        "type": "string"
                    },
                    "currency": {
                        "type": "string"
                    },
                    "notes": {
                        "type": "string"
                    }
                },
                "required": [
                    "table_title",
                    "page_start",
                    "page_end",
                    "columns",
                    "rows",
                    "unit",
                    "currency",
                    "notes"
                ],
                "additionalProperties": False
            }
        }
    },
    "required": [
        "tables"
    ],
    "additionalProperties": False
}


# ============================================================
# HELPERS
# ============================================================

def clean_cell(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\x00", "")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def clean_table(table):
    cleaned = []

    for row in table:
        if not row:
            continue

        cleaned_row = [
            clean_cell(cell)
            for cell in row
        ]

        # Skip completely empty rows
        if not any(cleaned_row):
            continue

        cleaned.append(cleaned_row)

    return cleaned


def table_to_text(table):
    """
    Convert extracted table into a compact textual representation
    that can be sent to the LLM.
    """

    lines = []

    for row in table:
        lines.append(
            " | ".join(
                clean_cell(cell)
                for cell in row
            )
        )

    return "\n".join(lines)


# ============================================================
# LOCAL TABLE EXTRACTION
# ============================================================

def extract_tables_from_pdf(file_bytes):
    """
    Extract tables locally using pdfplumber.

    Returns:

    [
        {
            "page_number": 1,
            "table_index": 1,
            "rows": [...]
        }
    ]
    """

    extracted_tables = []

    with pdfplumber.open(
        __import__("io").BytesIO(file_bytes)
    ) as pdf:

        table_index = 0

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            try:
                tables = page.extract_tables()
            except Exception as e:
                print(
                    f"Table extraction failed on page "
                    f"{page_number}: {e}"
                )
                continue

            if not tables:
                continue

            for table in tables:

                cleaned = clean_table(table)

                if not cleaned:
                    continue

                # Ignore tiny fragments
                if len(cleaned) < 2:
                    continue

                table_index += 1

                extracted_tables.append({
                    "page_number": page_number,
                    "table_index": table_index,
                    "rows": cleaned,
                })

    return extracted_tables


# ============================================================
# TABLE PAGE DETECTION FALLBACK
# ============================================================

def detect_table_like_pages(file_bytes):
    """
    Fallback detector for pages where pdfplumber did not
    successfully extract a table.

    This does NOT send anything to the LLM yet.
    """

    pages = []

    pdf = fitz.open(
        stream=file_bytes,
        filetype="pdf"
    )

    try:

        for page_number, page in enumerate(
            pdf,
            start=1
        ):

            text = page.get_text("text") or ""

            if not text.strip():
                continue

            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            if len(lines) < 4:
                continue

            # Heuristic indicators
            numeric_lines = 0
            multi_column_lines = 0

            for line in lines:

                numbers = re.findall(
                    r"\b\d[\d,().-]*\b",
                    line
                )

                if len(numbers) >= 2:
                    numeric_lines += 1

                if (
                    len(line.split()) >= 4
                    and len(numbers) >= 1
                ):
                    multi_column_lines += 1

            if (
                numeric_lines >= 3
                or multi_column_lines >= 4
            ):
                pages.append({
                    "page_number": page_number,
                    "text": text,
                })

    finally:
        pdf.close()

    return pages


# ============================================================
# LLM TABLE NORMALIZATION
# ============================================================

def normalize_table_with_llm(
    table_rows,
    page_start,
    page_end,
    raw_text,
):
    """
    Convert a locally extracted table into structured JSON.

    The LLM must preserve values and must not invent information.
    """

    prompt = f"""
You are a financial-document table extraction system.

Convert the supplied table into structured JSON.

STRICT RULES:

1. Preserve every value from the source.
2. Do not calculate or infer missing values.
3. Do not invent rows or columns.
4. Preserve negative numbers.
5. Preserve parentheses when they represent negative values.
6. Preserve financial years exactly.
7. Preserve units such as:
   - thousands
   - millions
   - PHP
   - USD
8. If the table has a title, preserve it.
9. If the title is not available, use an accurate short description.
10. Keep the original numeric values as strings.
11. Do not convert "1,234" into "1234".
12. Do not merge unrelated tables.
13. If there are notes directly associated with the table,
    put them in the notes field.
14. Do not use outside knowledge.

Page:
{page_start}

Extracted table:

{raw_text}
"""

    response = openai_client.responses.create(
        model="gpt-5.6-luna",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "financial_tables",
                "strict": True,
                "schema": TABLE_SCHEMA,
            }
        },
    )

    output_text = response.output_text

    result = json.loads(
        output_text
    )

    return result


# ============================================================
# PAGE-BASED LLM TABLE EXTRACTION
# ============================================================

def extract_tables_from_page_with_llm(
    page_number,
    page_text,
):
    """
    Used when local table extraction failed but the page
    appears to contain a table.
    """

    prompt = f"""
You are extracting tables from a financial report.

The following is text extracted from PDF page {page_number}.

Determine whether there are one or more actual tables.

If there are no tables, return:

{{
    "tables": []
}}

If there are tables:

- preserve all values exactly
- preserve column headers
- preserve row labels
- preserve years
- preserve units
- preserve currency
- do not invent missing values
- do not perform calculations
- separate different tables
- identify the table title when possible

PAGE TEXT:

{page_text}
"""

    response = openai_client.responses.create(
        model="gpt-5.6-luna",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "financial_tables",
                "strict": True,
                "schema": TABLE_SCHEMA,
            }
        },
    )

    return json.loads(
        response.output_text
    )


# ============================================================
# COMPLETE TABLE EXTRACTION
# ============================================================

def extract_all_tables(
    file_name,
    file_bytes
):
    """
    Complete table extraction pipeline.

    1. Local extraction
    2. LLM normalization
    3. Fallback LLM page extraction
    """

    extension = os.path.splitext(
        file_name
    )[1].lower()

    if extension != ".pdf":
        return []

    final_tables = []

    # --------------------------------------------------------
    # STEP 1
    # Local extraction
    # --------------------------------------------------------

    local_tables = extract_tables_from_pdf(
        file_bytes
    )

    print(
        f"LOCAL TABLES FOUND: "
        f"{len(local_tables)}"
    )

    # --------------------------------------------------------
    # STEP 2
    # Normalize locally extracted tables
    # --------------------------------------------------------

    for table in local_tables:

        raw_text = table_to_text(
            table["rows"]
        )

        if not raw_text.strip():
            continue

        try:

            normalized = normalize_table_with_llm(
                table_rows=table["rows"],
                page_start=table["page_number"],
                page_end=table["page_number"],
                raw_text=raw_text,
            )

            for llm_table in normalized.get(
                "tables",
                []
            ):

                final_tables.append({
                    "table_index": len(final_tables) + 1,
                    "table_title": llm_table.get(
                        "table_title",
                        ""
                    ),
                    "page_start": llm_table.get(
                        "page_start",
                        table["page_number"]
                    ),
                    "page_end": llm_table.get(
                        "page_end",
                        table["page_number"]
                    ),
                    "columns": llm_table.get(
                        "columns",
                        []
                    ),
                    "rows": llm_table.get(
                        "rows",
                        []
                    ),
                    "unit": llm_table.get(
                        "unit",
                        ""
                    ),
                    "currency": llm_table.get(
                        "currency",
                        ""
                    ),
                    "notes": llm_table.get(
                        "notes",
                        ""
                    ),
                    "raw_table": raw_text,
                })

        except Exception as e:

            print(
                f"LLM table normalization error "
                f"on page {table['page_number']}: "
                f"{e}"
            )

    # --------------------------------------------------------
    # STEP 3
    # Detect pages that may contain tables
    # --------------------------------------------------------

    extracted_page_numbers = {
        table["page_number"]
        for table in local_tables
    }

    candidate_pages = detect_table_like_pages(
        file_bytes
    )

    # --------------------------------------------------------
    # STEP 4
    # LLM fallback for candidate pages
    # --------------------------------------------------------

    for page in candidate_pages:

        page_number = page["page_number"]

        if page_number in extracted_page_numbers:
            continue

        try:

            result = extract_tables_from_page_with_llm(
                page_number=page_number,
                page_text=page["text"],
            )

            for llm_table in result.get(
                "tables",
                []
            ):

                final_tables.append({
                    "table_index": len(final_tables) + 1,
                    "table_title": llm_table.get(
                        "table_title",
                        ""
                    ),
                    "page_start": llm_table.get(
                        "page_start",
                        page_number
                    ),
                    "page_end": llm_table.get(
                        "page_end",
                        page_number
                    ),
                    "columns": llm_table.get(
                        "columns",
                        []
                    ),
                    "rows": llm_table.get(
                        "rows",
                        []
                    ),
                    "unit": llm_table.get(
                        "unit",
                        ""
                    ),
                    "currency": llm_table.get(
                        "currency",
                        ""
                    ),
                    "notes": llm_table.get(
                        "notes",
                        ""
                    ),
                    "raw_table": page["text"],
                })

        except Exception as e:

            print(
                f"LLM page table extraction error "
                f"on page {page_number}: {e}"
            )

    print(
        f"FINAL TABLE COUNT: "
        f"{len(final_tables)}"
    )

    return final_tables

def save_sec_document_tables(
    cursor,
    kx_topic_id,
    tables,
):
    """
    Save extracted tables into sec_document_tables.
    """

    for table in tables:

        table_data = {
            "columns": table.get(
                "columns",
                []
            ),
            "rows": table.get(
                "rows",
                []
            ),
            "unit": table.get(
                "unit",
                ""
            ),
            "currency": table.get(
                "currency",
                ""
            ),
            "notes": table.get(
                "notes",
                ""
            ),
        }

        metadata = {
            "source": "pdf",
            "extraction_method": "local_plus_llm",
        }

        cursor.execute(
            """
            INSERT INTO sec_document_tables (
                kx_topic_id,
                table_index,
                table_title,
                page_start,
                page_end,
                table_data,
                raw_table,
                metadata
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s::jsonb,
                %s,
                %s::jsonb
            )
            """,
            (
                kx_topic_id,
                table["table_index"],
                table.get(
                    "table_title"
                ),
                table.get(
                    "page_start"
                ),
                table.get(
                    "page_end"
                ),
                json.dumps(
                    table_data,
                    ensure_ascii=False
                ),
                table.get(
                    "raw_table",
                    ""
                ),
                json.dumps(
                    metadata,
                    ensure_ascii=False
                ),
            )
        )