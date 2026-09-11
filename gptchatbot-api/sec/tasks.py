from celery import shared_task
from django.db import connections, transaction
import fitz

from rag.extractions.table_extraction import (
    extract_all_tables,
    save_sec_document_tables,
)
from rag.extractions.gis_extraction import extract_gis_structured_data
from rag.utils import chunk_text
from rag.embeddings import get_embedding

from general.utilities.ipfs_utilities import download_from_ipfs

from sec.extraction.pdf_extractor import (OPENAI_MODEL,
                                          extract_pdf_text, 
                                          extract_tables_from_pdf, 
                                          detect_table_pages,
                                          expand_table_pages,
                                          create_table_pages_pdf)
from sec.utils import remove_nul_chars

import os, json

# =========================================================
# CELERY INGESTION TASK
# =========================================================
@shared_task(
    bind=True,
    max_retries=3,
)
def ingest_sec_document(
    self,
    topic_id,
    file_name,
    file_path,
):
    """
    Complete SEC knowledge-base ingestion.

    PDF
      |
      +--> normal text/OCR
      |       |
      |       +--> chunks
      |              |
      |              +--> embeddings
      |                     |
      |                     +--> sec_document
      |
      +--> original PDF
              |
              +--> multimodal LLM
                     |
                     +--> sec_document_tables
    """

    conn = connections["birai_db"]

    try:

        # ---------------------------------------------------------
        # 1. Mark ingestion as PROCESSING
        # ---------------------------------------------------------

        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE kx_topics
                SET ingestion_status = %s
                WHERE id = %s
                """,
                [
                    "PROCESSING",
                    topic_id,
                ],
            )

        # ---------------------------------------------------------
        # 2. Read original PDF
        # ---------------------------------------------------------

        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"SEC PDF not found: {file_path}"
            )

        with open(
            file_path,
            "rb",
        ) as pdf_file:
            file_bytes = pdf_file.read()

        if not file_bytes:
            raise ValueError(
                "SEC PDF is empty."
            )

        # 3. Extract text/OCR
        pages = extract_pdf_text(file_bytes)

        if not pages:
            raise ValueError(
                "No text could be extracted from the PDF."
            )


        # 4. Build chunks
        document_chunks = []

        for page in pages:

            chunks = chunk_text(
                page["text"]
            )

            for chunk_index, chunk in enumerate(
                chunks
            ):

                document_chunks.append({
                    "page_number": page["page_number"],
                    "chunk_index": chunk_index,
                    "content": remove_nul_chars(chunk),
                    "extraction_method": page[
                        "extraction_method"
                    ],
                })


        if not document_chunks:
            raise ValueError(
                "No document chunks were created."
            )


        # 5. Generate embeddings
        texts = [
            item["content"]
            for item in document_chunks
        ]

        embeddings = get_embedding(
            texts
        )

        if len(embeddings) != len(
            document_chunks
        ):
            raise ValueError(
                "Embedding count does not match "
                "document chunk count."
            )


        # 6. Detect table pages locally
        print(
            "SEC TABLE DETECTION: "
            "starting local table-page detection..."
        )

        table_pages = detect_table_pages(
            file_bytes
        )

        print(
            "SEC TABLE DETECTION: "
            f"initial detected pages: {table_pages}"
        )


        # 7. Expand table pages
        pdf_check = fitz.open(
            stream=file_bytes,
            filetype="pdf",
        )

        try:
            total_pages = len(pdf_check)
        finally:
            pdf_check.close()


        table_pages = expand_table_pages(
            table_pages,
            total_pages=total_pages,
            radius=1,
        )

        print(
            "SEC TABLE DETECTION: "
            f"expanded pages: {table_pages}"
        )


        # 8. Extract tables only from selected pages
        tables = []

        if table_pages:

            table_pdf_path = None

            try:

                table_pdf_path = create_table_pages_pdf(
                    file_bytes,
                    table_pages,
                )

                tables = extract_tables_from_pdf(
                    table_pdf_path,
                    source_page_numbers=table_pages,
                )

            finally:

                if (
                    table_pdf_path
                    and os.path.exists(table_pdf_path)
                ):
                    os.remove(table_pdf_path)

        else:

            print(
                "SEC TABLE DETECTION: "
                "no table-like pages detected; "
                "skipping Terra table extraction."
            )

        # ---------------------------------------------------------
        # 7. Save everything atomically
        # ---------------------------------------------------------

        with transaction.atomic(
            using="birai_db"
        ):

            with conn.cursor() as cursor:

                # ---------------------------------------------
                # Make retry/idempotency safe.
                # ---------------------------------------------

                cursor.execute(
                    """
                    DELETE FROM sec_document_tables
                    WHERE kx_topic_id = %s
                    """,
                    [topic_id],
                )

                cursor.execute(
                    """
                    DELETE FROM sec_document
                    WHERE kx_topic_id = %s
                    """,
                    [topic_id],
                )

                # ---------------------------------------------
                # Save general document chunks
                # ---------------------------------------------

                for index, (
                    item,
                    embedding,
                ) in enumerate(
                    zip(
                        document_chunks,
                        embeddings,
                    ),
                    start=1,
                ):

                    metadata = {
                        "source": "sec_pdf",
                        "file_name": file_name,
                        "extraction_method": item[
                            "extraction_method"
                        ],
                    }

                    cursor.execute(
                        """
                        INSERT INTO sec_document (
                            kx_topic_id,
                            content,
                            page_number,
                            section_name,
                            chunk_index,
                            metadata,
                            embedding
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s::jsonb,
                            %s
                        )
                        """,
                        [
                            topic_id,
                            item["content"],
                            item["page_number"],
                            None,
                            index,
                            json.dumps(metadata),
                            embedding,
                        ],
                    )

                # ---------------------------------------------
                # Save tables
                # ---------------------------------------------

                for table_number, table in enumerate(
                    tables,
                    start=1,
                ):

                    table_index = table.get(
                        "table_index"
                    ) or table_number

                    table_title = remove_nul_chars(
                        table.get("table_title") or ""
                    )

                    page_start = table.get(
                        "page_start"
                    )

                    page_end = table.get(
                        "page_end"
                    )

                    columns = remove_nul_chars(
                        table.get("columns", [])
                    )

                    rows = remove_nul_chars(
                        table.get("rows", [])
                    )

                    raw_table = remove_nul_chars(
                        table.get("raw_table") or ""
                    )

                    table_data = {
                        "columns": columns,
                        "rows": rows,
                    }

                    metadata = {
                        "source": "sec_llm_table_extraction",
                        "model": OPENAI_MODEL,
                        "file_name": file_name,
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
                        [
                            topic_id,
                            table_index,
                            table_title,
                            page_start,
                            page_end,
                            json.dumps(table_data),
                            raw_table,
                            json.dumps(metadata),
                        ],
                    )

                # ---------------------------------------------
                # Mark complete
                # ---------------------------------------------

                cursor.execute(
                    """
                    UPDATE kx_topics
                    SET ingestion_status = %s
                    WHERE id = %s
                    """,
                    [
                        "COMPLETED",
                        topic_id,
                    ],
                )

        # ---------------------------------------------------------
        # 8. Delete temporary PDF
        # ---------------------------------------------------------

        if os.path.exists(file_path):
            os.remove(file_path)

        return {
            "status": "success",
            "topic_id": topic_id,
            "chunks": len(document_chunks),
            "tables": len(tables),
        }

    except Exception as exc:

        print(
            f"SEC ingestion failed "
            f"(topic_id={topic_id}): {exc}"
        )

        # ---------------------------------------------------------
        # Mark failed
        # ---------------------------------------------------------

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE kx_topics
                    SET ingestion_status = %s
                    WHERE id = %s
                    """,
                    [
                        "FAILED",
                        topic_id,
                    ],
                )
        except Exception as status_exc:
            print(
                "Failed to update ingestion status: "
                f"{status_exc}"
            )

        # ---------------------------------------------------------
        # Retry
        # ---------------------------------------------------------

        raise self.retry(
            exc=exc,
            countdown=60,
        )



@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={
        "max_retries": 3
    },
)
def extract_and_save_tables(
    self,
    topic_id,
    file_name,
    file_cid,
    file_path
):

    print("=" * 70)
    print("TABLE EXTRACTION TASK")
    print(f"Topic ID: {topic_id}")
    print(f"File: {file_name}")
    print("=" * 70)


    # ===============================
    # DOWNLOAD FILE
    # ===============================
    if file_cid:
        file_bytes = download_from_ipfs(
            file_cid
        )
    else:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

    # ===============================
    # EXTRACT TABLES
    # ===============================

    tables = extract_all_tables(
        file_name=file_name,
        file_bytes=file_bytes
    )


    print(
        f"Tables found: {len(tables)}"
    )


    # ===============================
    # SAVE DATABASE
    # ===============================

    with connections["birai_db"].cursor() as cursor:

        save_sec_document_tables(
            cursor=cursor,
            kx_topic_id=topic_id,
            tables=tables,
        )


    return {
        "topic_id": topic_id,
        "tables": len(tables),
    }
    
@shared_task(
    bind=True,
    max_retries=3,
)
def extract_and_save_gis(
    self,
    topic_id,
    file_name,
    file_path,
):

    try:

        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Temporary file not found: {file_path}"
            )

        structured_pages = (
            extract_gis_structured_data(
                file_path
            )
        )

        with connections["birai_db"].cursor() as cursor:

            table_index = 1

            for page in structured_pages:

                page_number = page["page_number"]

                for section in page["sections"]:

                    title = section.get(
                        "title",
                        "",
                    )

                    section_type = section.get(
                        "type",
                        "text",
                    )

                    content = section.get(
                        "content",
                        "",
                    )

                    raw_data = section.get(
                        "data",
                        "{}",
                    )

                    try:
                        structured_data = (
                            json.loads(raw_data)
                        )
                    except (
                        json.JSONDecodeError,
                        TypeError,
                    ):
                        structured_data = {
                            "raw_data": raw_data
                        }

                    table_data = {
                        "type": section_type,
                        "data": structured_data,
                    }

                    metadata = {
                        "source": "gis_llm_extraction",
                        "model": os.getenv(
                            "GIS_EXTRACTION_MODEL",
                            "gpt-5.6-luna",
                        ),
                        "file_name": file_name,
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
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            topic_id,
                            table_index,
                            title,
                            page_number,
                            page_number,
                            json.dumps(table_data),
                            content,
                            json.dumps(metadata),
                        ),
                    )

                    table_index += 1

        # Delete only after successful processing.
        try:
            os.remove(file_path)
        except OSError as exc:
            print(
                f"Could not delete temporary file "
                f"{file_path}: {exc}"
            )

        return {
            "status": "success",
            "topic_id": topic_id,
            "sections": table_index - 1,
        }

    except Exception as exc:

        print(
            f"GIS extraction failed "
            f"for topic {topic_id}: {exc}"
        )

        raise self.retry(
            exc=exc,
            countdown=60,
        )