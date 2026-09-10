import json
import os
import re
from typing import Any, Dict, List

import fitz
import pdfplumber
from openai import OpenAI
from gptchatbot.settings import OPENAI_API_KEY

openai_client = OpenAI(api_key=OPENAI_API_KEY)

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


GIS_SYSTEM_PROMPT = """
You are a document-structure extraction system for GIS documents.

Read the supplied PDF page text and convert the meaningful
information into structured JSON.

GIS documents may contain:

- paragraphs
- headings
- key-value information
- lists
- tables
- geographic information
- administrative information
- coordinates
- measurements
- areas
- population information
- land classifications
- project information
- dates
- names of locations
- mixed structured and unstructured information

Do NOT assume that everything is a table.

For each logical section:

1. Give the section a meaningful title.
2. Identify its type:
   - text
   - table
   - key_value
   - list
   - mixed

3. Preserve factual information from the source.

4. Do not invent missing values.

5. Do not calculate values.

6. Do not change units.

7. Preserve numbers as they appear in the source.

8. Preserve geographic names as accurately as possible.

9. If information is ambiguous, preserve the ambiguity
   instead of guessing.

10. For tables or semi-structured information, preserve
    relationships between values.

11. The "content" field should contain a concise textual
    representation of the section.

12. The "data" field should contain a JSON-formatted string
    containing the structured information for that section.

13. If the section does not have meaningful structured data,
    use an empty JSON object "{}" for "data".

Only use information contained in the supplied page.
"""


def extract_gis_pages(file_path):
    """
    Extract text from each page of a GIS PDF.

    The PDF is opened directly from disk so the entire PDF
    does not need to be loaded into memory.
    """

    pages = []

    pdf = fitz.open(file_path)

    try:
        for page_number, page in enumerate(pdf, start=1):

            text = page.get_text("text")

            text = (
                text
                .replace("\x00", "")
                .strip()
            )

            if not text:
                continue

            pages.append({
                "page_number": page_number,
                "content": text,
            })

    finally:
        pdf.close()

    return pages


def normalize_gis_page(
    page_number,
    page_text,
):
    """
    Send one GIS page to GPT and return structured JSON.
    """

    prompt = f"""
{GIS_SYSTEM_PROMPT}

PDF PAGE NUMBER:
{page_number}

PDF PAGE CONTENT:
-------------------------

{page_text}

-------------------------

Extract and structure all meaningful information
from this page.
"""

    response = openai_client.responses.create(
        model="gpt-5.6-luna",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "gis_page",
                "strict": True,
                "schema": GIS_PAGE_SCHEMA,
            }
        },
    )

    return json.loads(
        response.output_text
    )


def extract_gis_structured_data(file_path):
    """
    Extract structured GIS information from the PDF.
    """

    pages = extract_gis_pages(file_path)

    results = []

    for page in pages:

        page_number = page["page_number"]

        try:

            structured = normalize_gis_page(
                page_number=page_number,
                page_text=page["content"],
            )

            results.append(structured)

        except Exception as exc:

            print(
                f"GIS extraction failed "
                f"on page {page_number}: {exc}"
            )

    return results