from django.db import connections, transaction

from sec.models import KXTopics, SecDocument
from sec.utils import extract_sec_query_filters, search_sec_knowledge_base, get_sec_query_metadata
from sec.tasks import extract_and_save_tables, extract_and_save_gis, ingest_sec_document

from rag.embeddings import get_embedding
from rag.utils import extract_text, chunk_text
from rag.schemas import PromptInput
from rag.extractions.conversation_context_resolver import resolve_sec_conversation
from general.utilities.ipfs_utilities import upload_to_ipfs

from ninja import Router, Form, File
from ninja.files import UploadedFile
from chatbot_models.sec_model import sec_openai_gpt
import json, os, uuid, re

router = Router(tags=["Internal SEC AI"])
TEMP_DIR = "/app/temp_files"

# @router.post("/ingest-knowledge")
# def ingest_sec_knowledge(
#     request,

#     file: UploadedFile = File(...),

#     title: str = Form(...),

#     agent: str = Form(...),

#     uploaded_by: str = Form("Admin"),

#     company_name: str = Form(...),

#     sec_no: str = Form(...),

#     form_type: str = Form(...),

#     period_covered: str = Form(...)
# ):

#     conn = None

#     # ====================================================
#     # 1. READ FILE
#     # ====================================================

#     file_bytes = file.file.read()
#     TEMP_DIR = "/app/temp_files"
#     os.makedirs(TEMP_DIR, exist_ok=True)

#     temp_filename = f"{uuid.uuid4()}_{file.name}"
#     temp_file_path = os.path.join(
#         TEMP_DIR,
#         temp_filename,
#     )

#     with open(temp_file_path, "wb") as temp_file:
#         temp_file.write(file_bytes)

#     if not file_bytes:

#         return {
#             "status": "error",
#             "message": "Uploaded file is empty."
#         }


#     # ====================================================
#     # 2. EXTRACT TEXT
#     # ====================================================

#     pages = extract_text(
#         file.name,
#         file_bytes,
#         True
#     )

#     if not pages:

#         return {
#             "status": "error",
#             "message": "Text extraction failed."
#         }


#     # ====================================================
#     # 3. NORMALIZE FORM VALUES
#     # ====================================================

#     title = (
#         title.strip()
#         if title
#         else os.path.splitext(file.name)[0]
#     )

#     company_name = (
#         company_name.strip()
#         if company_name
#         else ""
#     )

#     sec_no = (
#         sec_no.strip()
#         if sec_no
#         else ""
#     )

#     form_type = (
#         form_type.strip()
#         if form_type
#         else ""
#     )

#     period_covered = (
#         period_covered.strip()
#         if period_covered
#         else ""
#     )

#     uploaded_by = (
#         uploaded_by.strip()
#         if uploaded_by
#         else "Admin"
#     )


#     # ====================================================
#     # 4. CHUNK EACH PAGE
#     # ====================================================

#     valid_chunks = []

#     global_chunk_index = 0

#     for page in pages:

#         page_number = page["page_number"]

#         page_content = (
#             page["content"]
#             .replace("\x00", "")
#             .strip()
#         )

#         if not page_content:
#             continue


#         chunks = chunk_text(
#             page_content,
#             chunk_size=800,
#             overlap=150
#         )


#         for chunk in chunks:

#             chunk = chunk.strip()

#             # Ignore very small chunks
#             if len(chunk) < 30:
#                 continue

#             valid_chunks.append({
#                 "chunk_index": global_chunk_index,
#                 "page_number": page_number,
#                 "content": chunk,
#             })

#             global_chunk_index += 1


#     # ====================================================
#     # 5. VALIDATE CHUNKS
#     # ====================================================

#     if not valid_chunks:
        
#         return {
#             "status": "error",
#             "message": "No valid document chunks were generated."
#         }

#     # ====================================================
#     # 6. UPLOAD ORIGINAL FILE TO IPFS
#     # ====================================================

#     # file_cid = upload_to_ipfs(
#     #     file_name=file.name,
#     #     file_bytes=file_bytes,
#     #     content_type=(
#     #         file.content_type
#     #         or "application/pdf"
#     #     )
#     # )
#     file_cid = None
#     # ====================================================
#     # 7. DATABASE CONNECTION
#     # ====================================================

#     conn = connections["birai_db"]


#     # ====================================================
#     # 8. DATABASE TRANSACTION
#     # ====================================================

#     with transaction.atomic(
#         using="birai_db"
#     ):

#         with conn.cursor() as cursor:

#             # =================================================
#             # 8.1 GET AGENT NAME
#             # =================================================

#             cursor.execute(
#                 """
#                 SELECT agent
#                 FROM kx_agents
#                 WHERE id = %s
#                 """,
#                 [agent]
#             )

#             row = cursor.fetchone()


#             if not row:

#                 return {
#                     "status": "error",
#                     "message": (
#                         f"Agent with ID {agent} "
#                         "was not found."
#                     )
#                 }


#             agent_name = row[0]


#             # =================================================
#             # 8.2 INSERT INTO kx_topics
#             # =================================================

#             cursor.execute(
#                 """
#                 INSERT INTO kx_topics (
#                     topic_title,
#                     agent,
#                     file_name,
#                     uploaded_by,
#                     agent_id,
#                     file_cid,
#                     company_name,
#                     sec_no,
#                     form_type,
#                     period_covered
#                 )
#                 VALUES (
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s,
#                     %s
#                 )
#                 RETURNING id
#                 """,
#                 [
#                     title,
#                     agent_name,
#                     file.name,
#                     uploaded_by,
#                     agent,
#                     file_cid,
#                     company_name,
#                     sec_no,
#                     form_type,
#                     period_covered,
#                 ]
#             )


#             topic_id = cursor.fetchone()[0]

#             # =================================================
#             # 8.3 EXTRACT STRUCTURED TABLES
#             # =================================================
            
#             if form_type == "GIS":
#                 transaction.on_commit(
#                     lambda: extract_and_save_gis.delay(
#                         topic_id=topic_id,
#                         file_name=file.name,
#                         file_path=temp_file_path,
#                     )
#                 )
#             else:
#                 transaction.on_commit(
#                     lambda: extract_and_save_tables.delay(
#                         topic_id=topic_id,
#                         file_name=file.name,
#                         file_cid=file_cid,
#                         file_path=temp_file_path,
#                     )
#                 )
            
#             # =================================================
#             # 8.4 INSERT DOCUMENT CHUNKS
#             # =================================================

#             inserted_chunks = 0

#             failed_embeddings = 0


#             for item in valid_chunks:

#                 chunk_index = item["chunk_index"]

#                 page_number = item["page_number"]

#                 chunk = item["content"]


#                 # =============================================
#                 # ENRICH TEXT BEFORE EMBEDDING
#                 # =============================================

#                 enriched_chunk = (
#                     f"Company: {company_name}. "
#                     f"SEC No.: {sec_no}. "
#                     f"Form Type: {form_type}. "
#                     f"Period Covered: {period_covered}. "
#                     f"Document: {title}. "
#                     f"Page: {page_number}. "
#                     f"Section Content:\n"
#                     f"{chunk}"
#                 )


#                 # =============================================
#                 # GENERATE EMBEDDING
#                 # =============================================

#                 vector = get_embedding(
#                     enriched_chunk
#                 )


#                 if not vector:

#                     failed_embeddings += 1

#                     continue


#                 # =============================================
#                 # NORMALIZE VECTOR
#                 # =============================================

#                 vector = [
#                     float(value)
#                     for value in vector
#                 ]


#                 # =============================================
#                 # VALIDATE VECTOR DIMENSION
#                 # =============================================

#                 if len(vector) != 1536:

#                     raise ValueError(
#                         "Invalid embedding dimension. "
#                         f"Expected 1536, "
#                         f"received {len(vector)}."
#                     )


#                 # =============================================
#                 # METADATA
#                 # =============================================

#                 metadata = {
#                     "file_name": file.name,
#                     "company_name": company_name,
#                     "sec_no": sec_no,
#                     "form_type": form_type,
#                     "period_covered": period_covered,
#                     "title": title,
#                     "page_number": page_number,
#                     "chunk_index": chunk_index,
#                 }


#                 # =============================================
#                 # INSERT INTO sec_document
#                 # =============================================

#                 cursor.execute(
#                     """
#                     INSERT INTO sec_document (
#                         kx_topic_id,
#                         content,
#                         page_number,
#                         section_name,
#                         chunk_index,
#                         metadata,
#                         embedding
#                     )
#                     VALUES (
#                         %s,
#                         %s,
#                         %s,
#                         %s,
#                         %s,
#                         %s,
#                         %s
#                     )
#                     """,
#                     [
#                         topic_id,
#                         chunk,
#                         page_number,
#                         None,
#                         chunk_index,
#                         json.dumps(metadata),
#                         vector,
#                     ]
#                 )


#                 inserted_chunks += 1


#     # ====================================================
#     # 9. SUCCESS RESPONSE
#     # ====================================================

#     return {
#         "status": "success",

#         "message": (
#             "SEC document successfully "
#             "ingested into the knowledge base."
#         ),

#         "topic_id": topic_id,

#         "file_name": file.name,

#         "file_cid": file_cid,

#         "company_name": company_name,

#         "sec_no": sec_no,

#         "form_type": form_type,

#         "period_covered": period_covered,

#         "pages": len(pages),

#         "chunks_generated": len(valid_chunks),

#         "chunks_inserted": inserted_chunks,

#         "chunks_failed_embedding": failed_embeddings,
#     }


#     # ========================================================
#     # 10. ERROR HANDLING
#     # ========================================================

@router.post("/ingest-knowledge")
def ingest_sec_knowledge(
    request,
    file: UploadedFile = File(...),
    title: str = Form(...),
    agent: str = Form(...),
    uploaded_by: str = Form("Admin"),
    company_name: str = Form(...),
    sec_no: str = Form(...),
    form_type: str = Form(...),
    period_covered: str = Form(...)
):
    """
    Upload and queue an SEC document for ingestion.

    The actual processing happens asynchronously in Celery:

        PDF
          ├──> text/OCR extraction
          │       └──> sec_document + embeddings
          │
          └──> multimodal LLM
                  └──> sec_document_tables
    """

    file_bytes = file.file.read()

    if not file_bytes:
        return {
            "status": "error",
            "message": "Uploaded file is empty.",
        }

    # =====================================================
    # NORMALIZE INPUT
    # =====================================================

    title = (
        title.strip()
        if title
        else os.path.splitext(file.name)[0]
    )

    company_name = (
        company_name.strip()
        if company_name
        else ""
    )

    sec_no = (
        sec_no.strip()
        if sec_no
        else ""
    )

    form_type = (
        form_type.strip()
        if form_type
        else ""
    )

    period_covered = (
        period_covered.strip()
        if period_covered
        else ""
    )

    uploaded_by = (
        uploaded_by.strip()
        if uploaded_by
        else "Admin"
    )

    # =====================================================
    # SAVE TEMP PDF
    # =====================================================

    TEMP_DIR = "/app/temp_files"

    os.makedirs(
        TEMP_DIR,
        exist_ok=True,
    )

    temp_filename = (
        f"{uuid.uuid4()}_{file.name}"
    )

    temp_file_path = os.path.join(
        TEMP_DIR,
        temp_filename,
    )

    try:

        with open(
            temp_file_path,
            "wb",
        ) as temp_file:

            temp_file.write(
                file_bytes
            )

        # =================================================
        # CREATE KNOWLEDGE TOPIC
        # =================================================

        conn = connections["birai_db"]

        with transaction.atomic(
            using="birai_db"
        ):

            with conn.cursor() as cursor:

                # -----------------------------------------
                # Validate agent
                # -----------------------------------------

                cursor.execute(
                    """
                    SELECT agent
                    FROM kx_agents
                    WHERE id = %s
                    """,
                    [agent],
                )

                row = cursor.fetchone()

                if not row:

                    raise ValueError(
                        f"Agent with ID {agent} "
                        f"was not found."
                    )

                agent_name = row[0]

                # -----------------------------------------
                # Create topic
                # -----------------------------------------

                cursor.execute(
                    """
                    INSERT INTO kx_topics (
                        topic_title,
                        agent,
                        file_name,
                        uploaded_by,
                        agent_id,
                        file_cid,
                        company_name,
                        sec_no,
                        form_type,
                        period_covered,
                        ingestion_status
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING id
                    """,
                    [
                        title,
                        agent_name,
                        file.name,
                        uploaded_by,
                        agent,
                        None,
                        company_name,
                        sec_no,
                        form_type,
                        period_covered,
                        "PENDING",
                    ],
                )

                topic_id = (
                    cursor.fetchone()[0]
                )

            # =============================================
            # QUEUE AFTER COMMIT
            # =============================================

            transaction.on_commit(
                lambda: ingest_sec_document.delay(
                    topic_id=topic_id,
                    file_name=file.name,
                    file_path=temp_file_path,
                )
            )

        # =================================================
        # RESPONSE
        # =================================================

        return {
            "status": "success",
            "message": (
                "SEC document queued for ingestion."
            ),
            "topic_id": topic_id,
            "file_name": file.name,
            "file_cid": None,
            "company_name": company_name,
            "sec_no": sec_no,
            "form_type": form_type,
            "period_covered": period_covered,
            "ingestion_status": "PENDING",
        }

    except Exception as exc:

        # If topic creation/queueing fails,
        # remove the temporary PDF.

        if os.path.exists(
            temp_file_path
        ):
            os.remove(
                temp_file_path
            )

        print(
            f"SEC ingestion endpoint failed: {exc}"
        )

        return {
            "status": "error",
            "message": str(exc),
        }
        
@router.post("/ask-sec")
def ask_sec(
    request,
    data: PromptInput,
):
    prompt = data.prompt
    ###########################################################
    # 1. VALIDATE PROMPT
    ###########################################################

    if not prompt or not prompt.strip():

        return {
            "response": "Please provide a question.",
            "match_type": "none",
            "score": 0,
            "documents": 0,
        }

    prompt = prompt.strip()


    ###########################################################
    # 2. CHAT HISTORY
    ###########################################################

    agent = data.agent

    try:

        history = json.loads(
            data.history
        )

        if not isinstance(history, list):
            history = []

    except Exception:

        history = []


    ###########################################################
    # 3. GET AGENT NAME
    ###########################################################

    try:

        with connections["birai_db"].cursor() as cursor:

            cursor.execute(
                """
                SELECT agent
                FROM kx_agents
                WHERE agent = %s
                """,
                (agent,),
            )

            row = cursor.fetchone()

            if not row:

                return {
                    "response": "Invalid SEC agent.",
                    "match_type": "none",
                    "score": 0,
                    "documents": 0,
                }

            agent_name = row[0]

    except Exception as e:

        print("=" * 70)
        print("SEC AGENT LOOKUP ERROR")
        print(str(e))
        print("=" * 70)

        return {
            "response": (
                "Unable to determine the selected "
                "SEC agent."
            ),
            "match_type": "none",
            "score": 0,
            "documents": 0,
        }


    ###########################################################
    # 4. RESOLVE CONVERSATION CONTEXT
    ###########################################################
    #
    # Example:
    #
    # User:
    #   Tell me about ABC Corporation.
    #
    # Assistant context:
    #   {
    #       "company_name": "ABC Corporation",
    #       "topic_ids": [123]
    #   }
    #
    # Next user:
    #   Give me its tables.
    #
    # resolve_sec_conversation() should produce:
    #
    #   referenced_company = ABC Corporation
    #   referenced_topic_ids = [123]
    #   resolved_question =
    #       "Give me the tables for ABC Corporation."
    #
    ###########################################################

    try:

        conversation_context = (
            resolve_sec_conversation(
                prompt=prompt,
                history=history,
            )
        )
        
        print("=" * 100)
        print("FOLLOW-UP RESOLUTION")
        print(json.dumps(conversation_context, indent=2, ensure_ascii=False))
        print("=" * 100)

        if not isinstance(
            conversation_context,
            dict,
        ):
            conversation_context = {}

        resolved_prompt = (
            conversation_context.get(
                "resolved_question"
            )
            or prompt
        )

        referenced_company = (
            conversation_context.get(
                "referenced_company"
            )
            or None
        )

        referenced_sec_no = (
            conversation_context.get(
                "referenced_sec_no"
            )
            or None
        )

        referenced_topic_ids = (
            conversation_context.get(
                "referenced_topic_ids"
            )
            or []
        )

    except Exception as e:

        print("=" * 70)
        print(
            "SEC CONVERSATION CONTEXT ERROR"
        )
        print(str(e))
        print("=" * 70)

        resolved_prompt = prompt

        referenced_company = None
        referenced_sec_no = None
        referenced_topic_ids = []


    ###########################################################
    # 5. NORMALIZE REFERENCED TOPIC IDS
    ###########################################################

    normalized_topic_ids = []

    for topic_id in referenced_topic_ids:

        try:

            normalized_topic_ids.append(
                int(topic_id)
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

    referenced_topic_ids = list(
        dict.fromkeys(
            normalized_topic_ids
        )
    )


    ###########################################################
    # 6. EXTRACT SEC QUERY FILTERS
    ###########################################################

    try:

        metadata = get_sec_query_metadata()

        filters = extract_sec_query_filters(
            prompt=resolved_prompt,

            known_companies=metadata[
                "companies"
            ],

            known_sec_numbers=metadata[
                "sec_numbers"
            ],

            known_form_types=metadata[
                "form_types"
            ],
        )

        if not isinstance(
            filters,
            dict,
        ):
            filters = {}

        #######################################################
        # Conversation context is the FALLBACK.
        #
        # If the user explicitly mentioned another company
        # in the current question, extract_sec_query_filters()
        # wins.
        #######################################################

        if (
            not filters.get(
                "company_name"
            )
            and referenced_company
        ):

            filters[
                "company_name"
            ] = referenced_company


        if (
            not filters.get(
                "sec_no"
            )
            and referenced_sec_no
        ):

            filters[
                "sec_no"
            ] = referenced_sec_no


        # Make sure all expected keys exist.

        filters.setdefault(
            "company_name",
            None,
        )

        filters.setdefault(
            "sec_no",
            None,
        )

        filters.setdefault(
            "form_type",
            None,
        )

        filters.setdefault(
            "period_covered",
            None,
        )

        filters.setdefault(
            "document",
            None,
        )

    except Exception as e:

        print("=" * 70)
        print("SEC QUERY FILTER ERROR")
        print(str(e))
        print("=" * 70)

        filters = {
            "company_name": (
                referenced_company
            ),

            "sec_no": (
                referenced_sec_no
            ),

            "form_type": None,

            "period_covered": None,

            "document": None,
        }


    ###########################################################
    # 7. STRUCTURED REQUEST
    ###########################################################

    structured_query = (
        conversation_context.get(
            "structured_query",
            {}
        )
        if isinstance(conversation_context, dict)
        else {}
    )

    if not isinstance(structured_query, dict):
        structured_query = {}

    is_table_request = bool(
        structured_query.get(
            "is_structured_request",
            False
        )
    )

    structured_subject = (
        structured_query.get("subject")
        or ""
    )

    structured_search_terms = (
        structured_query.get("search_terms")
        or []
    )

    if not isinstance(structured_search_terms, list):
        structured_search_terms = []

    structured_search_terms = [
        str(term).strip()
        for term in structured_search_terms
        if str(term).strip()
    ]


    ###########################################################
    # 8. DEBUG FILTERS / CONTEXT
    ###########################################################

    print("=" * 70)
    print("SEC QUERY FILTERS")

    print(
        f"Original Question : "
        f"{prompt}"
    )

    print(
        f"Resolved Question : "
        f"{resolved_prompt}"
    )

    print(
        f"Company           : "
        f"{filters.get('company_name')}"
    )

    print(
        f"SEC No.           : "
        f"{filters.get('sec_no')}"
    )

    print(
        f"Form Type         : "
        f"{filters.get('form_type')}"
    )

    print(
        f"Period            : "
        f"{filters.get('period_covered')}"
    )

    print(
        f"Document          : "
        f"{filters.get('document')}"
    )

    print(
        f"Referenced Topics : "
        f"{referenced_topic_ids}"
    )

    print(
        f"Table Request     : "
        f"{is_table_request}"
    )

    print(
        f"Structured Request: "
        f"{is_table_request}"
    )

    print(
        f"Structured Subject : "
        f"{structured_subject}"
    )

    print(
        f"Structured Terms   : "
        f"{structured_search_terms}"
    )
    
    print("=" * 70)


    ###########################################################
    # 9. RETRIEVE STRUCTURED TABLES
    ###########################################################

    table_results = []

    # ---------------------------------------------------------
    # Detect "all tables" request
    # ---------------------------------------------------------

    is_all_tables_request = bool(
        structured_query.get(
            "is_all_tables_request",
            False
    )
)
    print("=" * 70)
    print("STRUCTURED TABLE REQUEST")
    print("is_table_request    :", is_table_request)
    print("is_all_tables_request:", is_all_tables_request)
    print("structured terms    :", structured_search_terms)
    print("=" * 70)


    if is_table_request:

        try:

            with connections[
                "birai_db"
            ].cursor() as cursor:

                conditions = []
                params = []

                ################################################
                # REFERENCED TOPIC IDS
                ################################################

                if referenced_topic_ids:

                    placeholders = ", ".join(
                        ["%s"] * len(referenced_topic_ids)
                    )

                    conditions.append(
                        f"""
                        sdt.kx_topic_id IN (
                            {placeholders}
                        )
                        """
                    )

                    params.extend(
                        referenced_topic_ids
                    )

                ################################################
                # COMPANY
                ################################################

                if filters.get("company_name"):

                    conditions.append(
                        """
                        UPPER(TRIM(kt.company_name))
                        =
                        UPPER(TRIM(%s))
                        """
                    )

                    params.append(
                        filters["company_name"]
                    )

                ################################################
                # SEC NO.
                ################################################

                if filters.get("sec_no"):

                    conditions.append(
                        """
                        UPPER(TRIM(kt.sec_no))
                        =
                        UPPER(TRIM(%s))
                        """
                    )

                    params.append(
                        filters["sec_no"]
                    )

                ################################################
                # FORM TYPE
                ################################################

                if filters.get("form_type"):

                    conditions.append(
                        """
                        UPPER(TRIM(kt.form_type))
                        =
                        UPPER(TRIM(%s))
                        """
                    )

                    params.append(
                        filters["form_type"]
                    )

                ################################################
                # PERIOD
                ################################################

                if filters.get("period_covered"):

                    conditions.append(
                        """
                        UPPER(TRIM(kt.period_covered))
                        =
                        UPPER(TRIM(%s))
                        """
                    )

                    params.append(
                        filters["period_covered"]
                    )

                ################################################
                # BUILD WHERE
                ################################################

                where_clause = ""

                if conditions:

                    where_clause = (
                        "WHERE "
                        + " AND ".join(
                            conditions
                        )
                    )

                ################################################
                # ALL TABLES
                ################################################
                #
                # If the user explicitly asks for all tables,
                # DO NOT apply structured_search_terms.
                #
                # We retrieve every table belonging to the
                # identified filing/topic.
                #
                ################################################

                if is_all_tables_request:

                    # -------------------------------------------------
                    # Require at least one filing/topic filter.
                    # This prevents accidentally returning every table
                    # in the entire SEC database.
                    # -------------------------------------------------

                    if not conditions:

                        print(
                            "STRUCTURED TABLE SEARCH: "
                            "all-tables request but no filing filter"
                        )

                        table_results = []

                    else:

                        sql = f"""
                            SELECT
                                sdt.id,
                                sdt.kx_topic_id,
                                sdt.table_index,
                                sdt.table_title,
                                sdt.page_start,
                                sdt.page_end,
                                sdt.table_data,
                                sdt.raw_table,
                                sdt.metadata,

                                kt.company_name,
                                kt.sec_no,
                                kt.form_type,
                                kt.period_covered,
                                kt.topic_title,
                                kt.file_name

                            FROM sec_document_tables sdt

                            INNER JOIN kx_topics kt
                                ON kt.id = sdt.kx_topic_id

                            {where_clause}

                            ORDER BY
                                sdt.kx_topic_id ASC,
                                sdt.table_index ASC,
                                sdt.page_start ASC
                        """

                        cursor.execute(
                            sql,
                            params,
                        )

                        columns = [
                            column[0]
                            for column in cursor.description
                        ]

                        rows = cursor.fetchall()

                        table_results = [
                            dict(
                                zip(
                                    columns,
                                    row,
                                )
                            )
                            for row in rows
                        ]

                        ################################################
                        # DEBUG
                        ################################################

                        print(
                            "=" * 70
                        )

                        print(
                            "STRUCTURED TABLE RETRIEVAL - ALL TABLES"
                        )

                        print(
                            "Company       :",
                            filters.get("company_name"),
                        )

                        print(
                            "SEC No.       :",
                            filters.get("sec_no"),
                        )

                        print(
                            "Form type     :",
                            filters.get("form_type"),
                        )

                        print(
                            "Period        :",
                            filters.get("period_covered"),
                        )

                        print(
                            "Topic IDs     :",
                            referenced_topic_ids,
                        )

                        print(
                            "Tables found  :",
                            len(table_results),
                        )

                        for table in table_results:

                            print(
                                "TABLE:",
                                table.get(
                                    "table_index"
                                ),
                                "|",
                                table.get(
                                    "table_title"
                                ),
                                "| page=",
                                table.get(
                                    "page_start"
                                ),
                                "-",
                                table.get(
                                    "page_end"
                                ),
                            )

                        print(
                            "=" * 70
                        )

                ################################################
                # NORMAL TABLE SEARCH
                ################################################

                else:

                    ################################################
                    # BUILD SEARCH CONDITIONS
                    ################################################

                    search_conditions = []

                    search_params = []

                    for term in structured_search_terms:

                        term = (
                            str(term)
                            .strip()
                        )

                        if not term:
                            continue

                        search_pattern = (
                            f"%{term}%"
                        )

                        search_conditions.append(
                            """
                            (
                                sdt.table_title ILIKE %s
                                OR sdt.raw_table ILIKE %s
                                OR sdt.table_data::text ILIKE %s
                            )
                            """
                        )

                        search_params.extend([
                            search_pattern,
                            search_pattern,
                            search_pattern,
                        ])

                    ################################################
                    # TABLE SEARCH
                    ################################################

                    if search_conditions:

                        table_search_clause = (
                            "("
                            + " OR ".join(
                                search_conditions
                            )
                            + ")"
                        )

                        if where_clause:

                            where_clause += (
                                " AND "
                                + table_search_clause
                            )

                        else:

                            where_clause = (
                                "WHERE "
                                + table_search_clause
                            )

                    ################################################
                    # NO FILTERS / NO SEARCH TERMS
                    ################################################

                    if not where_clause:

                        print(
                            "STRUCTURED TABLE SEARCH: "
                            "no filters or search terms"
                        )

                        table_results = []

                    else:

                        ################################################
                        # SCORE TABLES
                        ################################################

                        score_parts = []

                        score_params = []

                        for term in structured_search_terms:

                            term = (
                                str(term)
                                .strip()
                            )

                            if not term:
                                continue

                            search_pattern = (
                                f"%{term}%"
                            )

                            score_parts.append(
                                """
                                CASE
                                    WHEN sdt.table_title ILIKE %s
                                    THEN 1.0
                                    ELSE 0.0
                                END
                                """
                            )

                            score_params.append(
                                search_pattern
                            )

                            score_parts.append(
                                """
                                CASE
                                    WHEN sdt.raw_table ILIKE %s
                                    THEN 0.60
                                    ELSE 0.0
                                END
                                """
                            )

                            score_params.append(
                                search_pattern
                            )

                            score_parts.append(
                                """
                                CASE
                                    WHEN sdt.table_data::text ILIKE %s
                                    THEN 0.40
                                    ELSE 0.0
                                END
                                """
                            )

                            score_params.append(
                                search_pattern
                            )

                        if score_parts:

                            relevance_expression = (
                                " + ".join(
                                    score_parts
                                )
                            )

                        else:

                            relevance_expression = "0.0"

                        ################################################
                        # EXECUTE
                        ################################################

                        sql = f"""
                            SELECT
                                sdt.id,
                                sdt.kx_topic_id,
                                sdt.table_index,
                                sdt.table_title,
                                sdt.page_start,
                                sdt.page_end,
                                sdt.table_data,
                                sdt.raw_table,
                                sdt.metadata,

                                kt.company_name,
                                kt.sec_no,
                                kt.form_type,
                                kt.period_covered,
                                kt.topic_title,
                                kt.file_name,

                                (
                                    {relevance_expression}
                                ) AS relevance_score

                            FROM sec_document_tables sdt

                            INNER JOIN kx_topics kt
                                ON kt.id = sdt.kx_topic_id

                            {where_clause}

                            ORDER BY
                                relevance_score DESC,
                                sdt.page_start ASC,
                                sdt.table_index ASC

                        """

                        final_params = (
                            score_params
                            + params
                            + search_params
                        )

                        cursor.execute(
                            sql,
                            final_params,
                        )

                        columns = [
                            column[0]
                            for column in cursor.description
                        ]

                        rows = cursor.fetchall()

                        table_results = [
                            dict(
                                zip(
                                    columns,
                                    row,
                                )
                            )
                            for row in rows
                        ]

                        ################################################
                        # DEBUG
                        ################################################

                        print(
                            "=" * 70
                        )

                        print(
                            "STRUCTURED TABLE RETRIEVAL"
                        )

                        print(
                            f"Search terms : "
                            f"{structured_search_terms}"
                        )

                        print(
                            f"Tables found : "
                            f"{len(table_results)}"
                        )

                        for table in table_results:

                            print(
                                "TABLE:",
                                table.get(
                                    "table_title"
                                ),
                                "| score=",
                                table.get(
                                    "relevance_score"
                                ),
                                "| page=",
                                table.get(
                                    "page_start"
                                ),
                            )

                        print(
                            "=" * 70
                        )

        except Exception as e:

            print(
                "=" * 70
            )

            print(
                "STRUCTURED TABLE RETRIEVAL ERROR"
            )

            print(
                str(e)
            )

            print(
                "=" * 70
            )

            table_results = []


    ###########################################################
    # 10. PREPARE STRUCTURED TABLE CONTEXT
    ###########################################################

    table_context = ""

    if table_results:

        table_context_parts = []

        for table in table_results:

            structured_data = (
                table.get(
                    "table_data"
                )
            )

            try:

                if isinstance(
                    structured_data,
                    str,
                ):

                    structured_data = (
                        json.loads(
                            structured_data
                        )
                    )

            except Exception:

                pass


            table_context_parts.append(
                f"""
STRUCTURED DOCUMENT SECTION

Company:
{table.get("company_name")}

SEC No:
{table.get("sec_no")}

Form Type:
{table.get("form_type")}

Period:
{table.get("period_covered")}

Document:
{table.get("topic_title")}

File:
{table.get("file_name")}

Page:
{table.get("page_start")}

Table/Section:
{table.get("table_title")}

Structured Data:
{json.dumps(
    structured_data,
    ensure_ascii=False,
    indent=2,
    default=str,
)}

Original Extracted Text:
{table.get("raw_table") or ""}
"""
            )


        table_context = "\n\n".join(
            table_context_parts
        )


    ###########################################################
    # 11. RETRIEVAL
    ###########################################################
    #
    # Always perform normal document retrieval.
    #
    # If this is a structured/table request, the structured
    # tables are added on top of the normal document context.
    #
    # This allows the LLM to see:
    #
    #     structured table
    #           +
    #     surrounding filing text
    #
    ###########################################################

    #######################################################
    # 11A. GENERATE QUERY EMBEDDING
    #######################################################

    query_embedding = None

    try:

        query_embedding = get_embedding(
            resolved_prompt
        )

    except Exception as e:

        print("=" * 70)

        print(
            "SEC EMBEDDING ERROR"
        )

        print(
            str(e)
        )

        print("=" * 70)


    #######################################################
    # 11B. INITIALIZE RETRIEVAL
    #######################################################

    contexts = []
    results = []

    match_type = "none"
    best_score = 0.0


    #######################################################
    # 11C. NORMAL DOCUMENT RETRIEVAL
    #######################################################
    if is_all_tables_request:

        print("=" * 70)
        print("SKIPPING NORMAL DOCUMENT RETRIEVAL")
        print("Reason: ALL TABLES request")
        print("=" * 70)

    else:

        if query_embedding:

            try:

                retrieval = search_sec_knowledge_base(
                    query_embedding=query_embedding,

                    user_question=resolved_prompt,

                    company_name=filters.get(
                        "company_name"
                    ),

                    sec_no=filters.get(
                        "sec_no"
                    ),

                    form_type=filters.get(
                        "form_type"
                    ),

                    period_covered=filters.get(
                        "period_covered"
                    ),

                    document=filters.get(
                        "document"
                    ),

                    limit=5,
                )

                contexts = retrieval.get(
                    "contexts",
                    []
                )

                results = retrieval.get(
                    "results",
                    []
                )

                match_type = retrieval.get(
                    "match_type",
                    "none"
                )

                best_score = retrieval.get(
                    "best_score",
                    0.0
                )

                print("=" * 70)
                print("RAW SEC RETRIEVAL RESULT")
                print(
                    "contexts   :",
                    len(contexts)
                )
                print(
                    "results    :",
                    len(results)
                )
                print(
                    "match_type :",
                    repr(match_type)
                )
                print(
                    "best_score :",
                    best_score
                )
                print("=" * 70)

            except Exception as e:

                print("=" * 70)
                print("SEC RETRIEVAL ERROR")
                print(str(e))
                print("=" * 70)


    #######################################################
    # 11D. ADD STRUCTURED TABLE CONTEXT
    #######################################################

    if table_results:

        if match_type == "none":
            match_type = "structured"

        elif match_type != "structured":
            match_type = "hybrid_structured"

        if best_score <= 0:
            best_score = 1.0


    #######################################################
    # 11E. DEBUG
    #######################################################

    print("=" * 70)

    print(
        "SEC FINAL RETRIEVAL"
    )

    print(
        f"Normal documents : "
        f"{len(results)}"
    )

    print(
        f"Structured tables: "
        f"{len(table_results)}"
    )

    print(
        f"Match type       : "
        f"{match_type}"
    )

    print(
        f"Best score       : "
        f"{best_score}"
    )

    print("=" * 70)


    ###########################################################
    # 12. BUILD FINAL LLM CONTEXT
    ###########################################################

    context_parts = []

    #######################################################
    # 12A. NORMAL DOCUMENT CONTEXT
    #######################################################

    for item in contexts:
        if isinstance(item, str):
            if item.strip():
                context_parts.append(item.strip())

        elif isinstance(item, dict):
            content = item.get("content")

            if content:
                context_parts.append(str(content).strip())
                
    #######################################################
    # 12B. STRUCTURED TABLE CONTEXT
    #######################################################

    if table_context:
        context_parts.append(
            "STRUCTURED SEC TABLE DATA:\n"
            + table_context.strip()
        )

    #######################################################
    # 12C. FINAL CONTEXT
    #######################################################

    if context_parts:
        context = "\n\n".join(
            part
            for part in context_parts
            if part
        )
    else:
        context = (
            "No relevant SEC documents "
            "were found in the Internal Knowledge Base."
        )

    #######################################################
    # 12D. DEBUG
    #######################################################

    print("=" * 70)
    print("SEC FINAL CONTEXT")
    print(
        f"Context sections : {len(context_parts)}"
    )
    print(
        f"Context chars    : {len(context)}"
    )
    print(
        f"Context tokens~  : {len(context) // 4}"
    )
    print("=" * 70)

    # ###########################################################
    # # 13. LIMIT FINAL LLM CONTEXT
    # ###########################################################

    # MAX_CONTEXT_CHARS = 48_000

    # if len(context) > MAX_CONTEXT_CHARS:
    #     print("=" * 70)
    #     print("SEC CONTEXT LIMIT")
    #     print(
    #         f"Original context : {len(context):,} chars"
    #     )
    #     print(
    #         f"Maximum context  : {MAX_CONTEXT_CHARS:,} chars"
    #     )
    #     print("=" * 70)

    #     context = context[:MAX_CONTEXT_CHARS]

    #     context += (
    #         "\n\n"
    #         "[CONTEXT TRUNCATED: Only the highest-priority "
    #         "retrieved content was included.]"
    #     )

    # print("=" * 70)
    # print("SEC LLM CONTEXT READY")
    # print(
    #     f"Final chars      : {len(context):,}"
    # )
    # print(
    #     f"Estimated tokens : {len(context) // 4:,}"
    # )
    # print("=" * 70)
    
    ###########################################################
    # 14. DEBUG INFORMATION
    ###########################################################

    print("=" * 70)
    print("SEC RETRIEVAL RESULT")

    print(
        f"Agent      : {agent_name}"
    )

    print(
        f"Question   : {prompt}"
    )

    print(
        f"Resolved   : {resolved_prompt}"
    )

    print(
        f"Company    : "
        f"{filters.get('company_name')}"
    )

    print(
        f"SEC No.    : "
        f"{filters.get('sec_no')}"
    )

    print(
        f"Form Type  : "
        f"{filters.get('form_type')}"
    )

    print(
        f"Period     : "
        f"{filters.get('period_covered')}"
    )

    print(
        f"Match Type : {match_type}"
    )

    print(
        f"Best Score : {best_score}"
    )

    print(
        f"Documents  : {len(contexts)}"
    )

    print(
        f"Tables     : {len(table_results)}"
    )

    print("=" * 70)

    has_structured_results = bool(table_results)
    has_normal_results = bool(results)

    no_docs_found = (
        not has_structured_results
        and not has_normal_results
    )

    try:
        print("VALUE OF NO DOCS FOUND: ", no_docs_found)
        
        if no_docs_found and not is_all_tables_request:
            response = sec_openai_gpt(
                prompt=resolved_prompt,
                history=history,
                match_type="none",
                from_ask_sec=True,
            )
        else:
            response = sec_openai_gpt(
                prompt=resolved_prompt,
                context=context,
                history=history,
                match_type=match_type,
                best_score=best_score,
                from_ask_sec=True,
            )

    except Exception as e:

        print("=" * 70)
        print("SEC GPT ERROR")
        print(str(e))
        print("=" * 70)

        return {
            "response": (
                "An error occurred while "
                "generating the response."
            ),

            "match_type": match_type,

            "score": best_score,

            "documents": len(contexts),
        }


    ###########################################################
    # 15. BUILD SOURCES
    ###########################################################

    sources = []


    ###########################################################
    # 15A. NORMAL sec_document SOURCES
    ###########################################################

    for result in results:

        sources.append({
            "source_type": "document",

            "document_id": result.get(
                "id"
            ),

            "topic_id": result.get(
                "kx_topic_id"
            ),

            "company_name": result.get(
                "company_name"
            ),

            "sec_no": result.get(
                "sec_no"
            ),

            "form_type": result.get(
                "form_type"
            ),

            "period_covered": result.get(
                "period_covered"
            ),

            "document": result.get(
                "topic_title"
            ),

            "file_name": result.get(
                "file_name"
            ),

            "page_number": result.get(
                "page_number"
            ),

            "section_name": result.get(
                "section_name"
            ),

            "score": result.get(
                "combined_score",
                0
            ),
        })


    ###########################################################
    # 15B. STRUCTURED TABLE SOURCES
    ###########################################################

    for table in table_results:

        sources.append({
            "source_type": "structured_table",

            "table_id": table.get(
                "id"
            ),

            "topic_id": table.get(
                "kx_topic_id"
            ),

            "company_name": table.get(
                "company_name"
            ),

            "sec_no": table.get(
                "sec_no"
            ),

            "form_type": table.get(
                "form_type"
            ),

            "period_covered": table.get(
                "period_covered"
            ),

            "document": table.get(
                "topic_title"
            ),

            "file_name": table.get(
                "file_name"
            ),

            "page_start": table.get(
                "page_start"
            ),

            "page_end": table.get(
                "page_end"
            ),

            "table_index": table.get(
                "table_index"
            ),

            "table_title": table.get(
                "table_title"
            ),

            "score": None,
        })


    ###########################################################
    # 16. BUILD CURRENT CONVERSATION CONTEXT
    ###########################################################

    current_topic_ids = set(
        referenced_topic_ids
    )


    # Add topic IDs from normal document retrieval.

    for source in sources:

        topic_id = source.get(
            "topic_id"
        )

        if topic_id is None:
            continue

        try:

            current_topic_ids.add(
                int(topic_id)
            )

        except (
            TypeError,
            ValueError,
        ):

            pass


    conversation_context = {
        "company_name": filters.get(
            "company_name"
        ),

        "sec_no": filters.get(
            "sec_no"
        ),

        "form_type": filters.get(
            "form_type"
        ),

        "period_covered": filters.get(
            "period_covered"
        ),

        "topic_ids": sorted(
            current_topic_ids
        ),
    }


    ###########################################################
    # 17. RETURN
    ###########################################################
    print(response)
    return {
        "response": response,

        "match_type": match_type,

        "score": best_score,

        "documents": len(contexts),

        "sources": sources,

        "filters": filters,

        "conversation_context": (
            conversation_context
        ),
    }