from django.db import connections, transaction

from sec.models import KXTopics, SecDocument
from rag.embeddings import get_embedding
from rag.utils import extract_text, chunk_text, upload_to_ipfs
from rag.schemas import PromptInput

from ninja import Router, Form, File
from ninja.files import UploadedFile
from chatbot_models.sec_model import sec_openai_gpt
from sec.utils import extract_sec_query_filters, search_sec_knowledge_base, get_sec_query_metadata
import json, os

router = Router(tags=["Internal SEC AI"])

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

    conn = None

    print("yez and2 na")
    # ====================================================
    # 1. READ FILE
    # ====================================================

    file_bytes = file.file.read()

    if not file_bytes:

        return {
            "status": "error",
            "message": "Uploaded file is empty."
        }


    # ====================================================
    # 2. EXTRACT TEXT
    # ====================================================

    pages = extract_text(
        file.name,
        file_bytes,
        True
    )

    if not pages:

        return {
            "status": "error",
            "message": "Text extraction failed."
        }


    # ====================================================
    # 3. NORMALIZE FORM VALUES
    # ====================================================

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


    # ====================================================
    # 4. CHUNK EACH PAGE
    # ====================================================

    valid_chunks = []

    global_chunk_index = 0

    for page in pages:

        page_number = page["page_number"]

        page_content = (
            page["content"]
            .replace("\x00", "")
            .strip()
        )

        if not page_content:
            continue


        chunks = chunk_text(
            page_content,
            chunk_size=800,
            overlap=150
        )


        for chunk in chunks:

            chunk = chunk.strip()

            # Ignore very small chunks
            if len(chunk) < 30:
                continue

            valid_chunks.append({
                "chunk_index": global_chunk_index,
                "page_number": page_number,
                "content": chunk,
            })

            global_chunk_index += 1


    # ====================================================
    # 5. VALIDATE CHUNKS
    # ====================================================

    if not valid_chunks:
        
        return {
            "status": "error",
            "message": "No valid document chunks were generated."
        }


    # ====================================================
    # 6. UPLOAD ORIGINAL FILE TO IPFS
    # ====================================================

    file_cid = upload_to_ipfs(
        file_name=file.name,
        file_bytes=file_bytes,
        content_type=(
            file.content_type
            or "application/pdf"
        )
    )

    # ====================================================
    # 7. DATABASE CONNECTION
    # ====================================================

    conn = connections["birai_db"]


    # ====================================================
    # 8. DATABASE TRANSACTION
    # ====================================================

    with transaction.atomic(
        using="birai_db"
    ):

        with conn.cursor() as cursor:

            # =================================================
            # 8.1 GET AGENT NAME
            # =================================================

            cursor.execute(
                """
                SELECT agent
                FROM kx_agents
                WHERE id = %s
                """,
                [agent]
            )

            row = cursor.fetchone()


            if not row:

                return {
                    "status": "error",
                    "message": (
                        f"Agent with ID {agent} "
                        "was not found."
                    )
                }


            agent_name = row[0]


            # =================================================
            # 8.2 INSERT INTO kx_topics
            # =================================================

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
                    period_covered
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
                    file_cid,
                    company_name,
                    sec_no,
                    form_type,
                    period_covered,
                ]
            )


            topic_id = cursor.fetchone()[0]


            # =================================================
            # 8.3 INSERT DOCUMENT CHUNKS
            # =================================================

            inserted_chunks = 0

            failed_embeddings = 0


            for item in valid_chunks:

                chunk_index = item["chunk_index"]

                page_number = item["page_number"]

                chunk = item["content"]


                # =============================================
                # ENRICH TEXT BEFORE EMBEDDING
                # =============================================

                enriched_chunk = (
                    f"Company: {company_name}. "
                    f"SEC No.: {sec_no}. "
                    f"Form Type: {form_type}. "
                    f"Period Covered: {period_covered}. "
                    f"Document: {title}. "
                    f"Page: {page_number}. "
                    f"Section Content:\n"
                    f"{chunk}"
                )


                # =============================================
                # GENERATE EMBEDDING
                # =============================================

                vector = get_embedding(
                    enriched_chunk
                )


                if not vector:

                    failed_embeddings += 1

                    continue


                # =============================================
                # NORMALIZE VECTOR
                # =============================================

                vector = [
                    float(value)
                    for value in vector
                ]


                # =============================================
                # VALIDATE VECTOR DIMENSION
                # =============================================

                if len(vector) != 1536:

                    raise ValueError(
                        "Invalid embedding dimension. "
                        f"Expected 1536, "
                        f"received {len(vector)}."
                    )


                # =============================================
                # METADATA
                # =============================================

                metadata = {
                    "file_name": file.name,
                    "company_name": company_name,
                    "sec_no": sec_no,
                    "form_type": form_type,
                    "period_covered": period_covered,
                    "title": title,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                }


                # =============================================
                # INSERT INTO sec_document
                # =============================================

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
                        %s,
                        %s
                    )
                    """,
                    [
                        topic_id,
                        chunk,
                        page_number,
                        None,
                        chunk_index,
                        json.dumps(metadata),
                        vector,
                    ]
                )


                inserted_chunks += 1


    # ====================================================
    # 9. SUCCESS RESPONSE
    # ====================================================

    return {
        "status": "success",

        "message": (
            "SEC document successfully "
            "ingested into the knowledge base."
        ),

        "topic_id": topic_id,

        "file_name": file.name,

        "file_cid": file_cid,

        "company_name": company_name,

        "sec_no": sec_no,

        "form_type": form_type,

        "period_covered": period_covered,

        "pages": len(pages),

        "chunks_generated": len(valid_chunks),

        "chunks_inserted": inserted_chunks,

        "chunks_failed_embedding": failed_embeddings,
    }


    # ========================================================
    # 10. ERROR HANDLING
    # ========================================================

        
        
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

        print(
            f"SEC agent lookup error: {str(e)}"
        )

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
    # 4. EXTRACT SEC QUERY FILTERS
    ###########################################################

    try:

        metadata = get_sec_query_metadata()

        filters = extract_sec_query_filters(
            prompt=prompt,

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


    except Exception as e:

        print("=" * 70)
        print("SEC QUERY FILTER ERROR")
        print(str(e))
        print("=" * 70)

        filters = {
            "company_name": None,
            "sec_no": None,
            "form_type": None,
            "period_covered": None,
            "document": None,
        }


    ###########################################################
    # 5. DEBUG FILTERS
    ###########################################################

    print("=" * 70)
    print("SEC QUERY FILTERS")
    print(
        f"Company      : "
        f"{filters.get('company_name')}"
    )
    print(
        f"SEC No.      : "
        f"{filters.get('sec_no')}"
    )
    print(
        f"Form Type    : "
        f"{filters.get('form_type')}"
    )
    print(
        f"Period       : "
        f"{filters.get('period_covered')}"
    )
    print(
        f"Document     : "
        f"{filters.get('document')}"
    )
    print("=" * 70)


    ###########################################################
    # 6. GENERATE QUERY EMBEDDING
    ###########################################################

    try:

        query_embedding = get_embedding(
            prompt
        )

        query_embedding = [
            float(value)
            for value in query_embedding
        ]


        if len(query_embedding) != 1536:

            raise ValueError(
                "Invalid query embedding dimension. "
                f"Expected 1536, got "
                f"{len(query_embedding)}."
            )


    except Exception as e:

        print(
            f"SEC embedding error: {str(e)}"
        )

        return {
            "response": (
                "Unable to process the question "
                "for knowledge-base retrieval."
            ),
            "match_type": "none",
            "score": 0,
            "documents": 0,
        }


    ###########################################################
    # 7. DOCUMENT REFERENCE
    ###########################################################

    document = filters.get(
        "document"
    )

    if document:

        print("=" * 70)
        print("SEC DOCUMENT DETECTED")
        print(document)
        print("=" * 70)


    ###########################################################
    # 8. RETRIEVE FROM POSTGRESQL + PGVECTOR
    ###########################################################

    try:

        retrieval = search_sec_knowledge_base(

            query_embedding=query_embedding,

            user_question=prompt,

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

            document=document,

            limit=5,
        )


    except Exception as e:

        print("=" * 70)
        print("SEC RETRIEVAL ERROR")
        print(str(e))
        print("=" * 70)

        retrieval = {
            "contexts": [],
            "results": [],
            "match_type": "none",
            "best_score": 0,
        }


    ###########################################################
    # 9. PREPARE RETRIEVAL RESULT
    ###########################################################

    contexts = retrieval.get(
        "contexts",
        []
    )

    match_type = retrieval.get(
        "match_type",
        "none"
    )

    best_score = retrieval.get(
        "best_score",
        0
    )

    results = retrieval.get(
        "results",
        []
    )

    no_docs_found = not contexts


    ###########################################################
    # 10. PREPARE CONTEXT
    ###########################################################

    if contexts:

        context = "\n\n".join(
            contexts
        )

    else:

        context = (
            "No relevant SEC documents were found "
            "in the Internal Knowledge Base."
        )


    ###########################################################
    # 11. DEBUG INFORMATION
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
    print("=" * 70)


    ###########################################################
    # 12. GENERATE ANSWER
    ###########################################################

    try:

        if no_docs_found:

            # -----------------------------------------------
            # No KB result
            # -----------------------------------------------

            response = sec_openai_gpt(
                prompt=prompt,
                history=history,
                match_type="none",
                from_ask_sec=True,
            )

        else:

            # -----------------------------------------------
            # KB result
            # -----------------------------------------------

            response = sec_openai_gpt(
                prompt=prompt,
                context=context,
                history=history,
                match_type=match_type,
                best_score=best_score,
                from_ask_sec=True,
            )


    except Exception as e:

        print(
            f"SEC GPT ERROR: {str(e)}"
        )

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
    # 13. BUILD SOURCES
    ###########################################################

    sources = []

    for result in results:

        sources.append({

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
    # 14. RETURN
    ###########################################################

    return {

        "response": response,

        "match_type": match_type,

        "score": best_score,

        "documents": len(contexts),

        "sources": sources,

        "filters": filters,

    }