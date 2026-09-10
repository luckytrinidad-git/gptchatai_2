import re
import json
import uuid
import psycopg2
from psycopg2.extras import execute_values

from django.db import connections, transaction
from django.conf import settings
from ninja import Router, Form, File
from ninja.files import UploadedFile

# Standardized Qdrant structure imports
import qdrant_client
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue, MatchText
from rag.qdrant_backend import run_exact_document_lookup, run_semantic_search, semantic_with_fallback

from rag.schemas import PromptInput
from rag.models import BIRDocument
from rag.utils import extract_text, chunk_text
from rag.embeddings import get_embedding
from general.utilities.ipfs_utilities import upload_to_ipfs
from chatbot_models.rag_model import openai_gpt45
from chatbot_models.openai_model import openai_gpt45 as bir_openai_gpt45

import os
from rag.utils import extract_document_reference 

router = Router(tags=["Internal BIR AI"])

def search_bir_knowledge_base(
    query_embedding,
    agent_name,
    user_question,
    document=None,
    limit=5,
):
    """
    PostgreSQL + pgvector retrieval for BIR documents.

    Retrieval strategy:

    1. If a document reference is detected:
       - Find matching topic_title first.
       - Retrieve chunks belonging only to that topic.
       - Rank those chunks semantically.
       - For comprehensive questions, retrieve more chunks.

    2. If no document reference is detected:
       - Perform semantic search across the selected agent.

    3. If an explicitly requested document exists but has no
       embedded chunks, do NOT fall back to unrelated documents.
    """

    ###########################################################
    # CONFIGURATION
    ###########################################################

    NORMAL_SEMANTIC_THRESHOLD = 0.50
    COMPREHENSIVE_SEMANTIC_THRESHOLD = 0.40

    ###########################################################
    # 0. DETERMINE RETRIEVAL SIZE
    ###########################################################

    question = (user_question or "").strip().lower()

    comprehensive_keywords = [
        "list all",
        "all provisions",
        "all sections",
        "everything about",
        "enumerate",
        "identify all",
        "what are all",
        "which provisions",
        "which sections",
        "all that",
        "all rules",
        "all requirements",
        "all cases",
        "all instances",
    ]

    is_comprehensive = any(
        keyword in question
        for keyword in comprehensive_keywords
    )

    if is_comprehensive:
        retrieval_limit = max(limit, 40)
        semantic_threshold = COMPREHENSIVE_SEMANTIC_THRESHOLD

        print("=" * 70)
        print("COMPREHENSIVE QUESTION DETECTED")
        print(f"Retrieval Limit : {retrieval_limit}")
        print(f"Similarity      : {semantic_threshold}")
        print("=" * 70)

    else:
        retrieval_limit = limit
        semantic_threshold = NORMAL_SEMANTIC_THRESHOLD

    ###########################################################
    # 1. GET AGENT ID
    ###########################################################

    with connections["birai_db"].cursor() as cursor:

        cursor.execute(
            """
            SELECT id
            FROM kx_agents
            WHERE agent = %s
            """,
            (agent_name,),
        )

        row = cursor.fetchone()

    if not row:

        print(
            f"WARNING: Agent not found: {agent_name}"
        )

        return {
            "contexts": [],
            "match_type": "none",
            "best_score": 0,
        }

    agent_id = row[0]

    ###########################################################
    # 2. DOCUMENT-AWARE SEARCH
    ###########################################################

    if document:

        print("=" * 70)
        print("DOCUMENT-AWARE SEARCH")
        print(f"Type    : {document['doc_type']}")
        print(f"Number  : {document['doc_number']}")
        print(f"Variants: {document['variants']}")
        print("=" * 70)

        #######################################################
        # 2A. FIND MATCHING TOPIC
        #######################################################

        topic_id = None
        topic_title = None

        with connections["birai_db"].cursor() as cursor:

            # First try exact normalized title matching.
            cursor.execute(
                """
                SELECT
                    id,
                    topic_title
                FROM kx_topics
                WHERE agent_id = %s
                  AND (
                    LOWER(
                        REGEXP_REPLACE(
                            topic_title,
                            '[^a-zA-Z0-9]+',
                            '',
                            'g'
                        )
                    )
                    =
                    LOWER(
                        REGEXP_REPLACE(
                            %s,
                            '[^a-zA-Z0-9]+',
                            '',
                            'g'
                        )
                    )
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    agent_id,
                    document["variants"][0],
                ),
            )

            row = cursor.fetchone()

        #######################################################
        # 2B. TRY ALL TITLE VARIANTS
        #######################################################

        if row:

            topic_id = row[0]
            topic_title = row[1]

        else:

            print(
                "No normalized exact title match."
            )

            doc_type = document["doc_type"]
            doc_number = document["doc_number"]

            with connections["birai_db"].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, topic_title
                    FROM kx_topics
                    WHERE agent_id = %s
                    AND LOWER(topic_title) LIKE LOWER(%s)
                    AND LOWER(topic_title) LIKE LOWER(%s)
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    [
                        agent_id,
                        f"%{doc_type}%",
                        f"%{doc_number}%",
                    ],
                )
                row = cursor.fetchone()

            if row:
                topic_id = row[0]
                topic_title = row[1]

        #######################################################
        # 2C. EXACT DOCUMENT FOUND
        #######################################################

        if topic_id:

            print("=" * 70)
            print("DOCUMENT MATCH FOUND")
            print(f"Topic ID    : {topic_id}")
            print(f"Topic Title : {topic_title}")
            print("=" * 70)

            ###################################################
            # Check whether this document actually has chunks
            # with embeddings.
            ###################################################

            with connections["birai_db"].cursor() as cursor:

                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM bir_document
                    WHERE kx_topics_id = %s
                      AND embedding IS NOT NULL
                    """,
                    (topic_id,),
                )

                embedded_chunk_count = cursor.fetchone()[0]

            if embedded_chunk_count == 0:

                print("=" * 70)
                print("DOCUMENT FOUND BUT NO EMBEDDED CHUNKS")
                print(f"Topic       : {topic_title}")
                print("=" * 70)

                # Do NOT search unrelated documents.
                return {
                    "contexts": [],
                    "match_type": "document_empty",
                    "best_score": 0,
                }

            ###################################################
            # Rank chunks using pgvector
            ###################################################

            with connections["birai_db"].cursor() as cursor:

                cursor.execute(
                    """
                    SELECT
                        d.id,
                        d.filename,
                        d.content,
                        d.chunk_index,
                        1 - (
                            d.embedding
                            <=> %s::vector
                        ) AS similarity
                    FROM bir_document d
                    WHERE d.kx_topics_id = %s
                      AND d.embedding IS NOT NULL
                    ORDER BY
                        d.embedding
                        <=> %s::vector
                    LIMIT %s
                    """,
                    [
                        query_embedding,
                        topic_id,
                        query_embedding,
                        retrieval_limit,
                    ],
                )

                ranked_rows = cursor.fetchall()

            ###################################################
            # Build context
            ###################################################

            if ranked_rows:

                contexts = []
                best_score = 0

                for row in ranked_rows:

                    similarity = float(row[4])

                    best_score = max(
                        best_score,
                        similarity,
                    )

                    contexts.append(
                        f"""
==================================================

Document Title:
{topic_title}

Filename:
{row[1]}

Chunk:
{row[3]}

Similarity Score:
{similarity:.3f}

Match Type:
EXACT DOCUMENT

Content:

{row[2]}

==================================================
"""
                    )

                print("=" * 70)
                print("EXACT DOCUMENT RETRIEVAL")
                print(f"Topic       : {topic_title}")
                print(f"Best Score  : {best_score:.3f}")
                print(f"Chunks      : {len(contexts)}")
                print(f"Comprehensive: {is_comprehensive}")
                print("=" * 70)

                return {
                    "contexts": contexts,
                    "match_type": "exact",
                    "best_score": round(
                        best_score,
                        3,
                    ),
                }

            # This should normally not happen because we already
            # checked for embedded chunks, but keep it safe.
            return {
                "contexts": [],
                "match_type": "document_empty",
                "best_score": 0,
            }

        #######################################################
        # 2D. EXPLICIT DOCUMENT NOT FOUND
        #######################################################

        print("=" * 70)
        print("EXPLICIT DOCUMENT NOT FOUND")
        print("Will NOT use unrelated semantic documents.")
        print("=" * 70)

        return {
            "contexts": [],
            "match_type": "document_not_found",
            "best_score": 0,
        }

    ###########################################################
    # 3. SEMANTIC SEARCH
    ###########################################################

    print("=" * 70)
    print("SEMANTIC SEARCH")
    print("No document reference detected.")
    print(f"Limit     : {retrieval_limit}")
    print(f"Threshold : {semantic_threshold}")
    print("=" * 70)

    ###########################################################
    # Search all chunks belonging to selected agent
    ###########################################################

    with connections["birai_db"].cursor() as cursor:

        cursor.execute(
            """
            SELECT
                d.id,
                d.filename,
                d.content,
                d.chunk_index,
                t.topic_title,
                1 - (
                    d.embedding
                    <=> %s::vector
                ) AS similarity
            FROM bir_document d
            INNER JOIN kx_topics t
                ON d.kx_topics_id = t.id
            WHERE t.agent_id = %s
              AND d.embedding IS NOT NULL
              AND (
                1 - (
                    d.embedding
                    <=> %s::vector
                )
              ) >= %s
            ORDER BY
                d.embedding
                <=> %s::vector
            LIMIT %s
            """,
            [
                query_embedding,
                agent_id,
                query_embedding,
                semantic_threshold,
                query_embedding,
                retrieval_limit,
            ],
        )

        rows = cursor.fetchall()

    ###########################################################
    # 4. NO SEMANTIC RESULTS
    ###########################################################

    if not rows:

        print(
            "No semantic documents found."
        )

        return {
            "contexts": [],
            "match_type": "none",
            "best_score": 0,
        }

    ###########################################################
    # 5. BUILD SEMANTIC CONTEXT
    ###########################################################

    contexts = []
    best_score = 0

    for row in rows:

        similarity = float(
            row[5]
        )

        best_score = max(
            best_score,
            similarity,
        )

        contexts.append(
            f"""
==================================================

Document Title:
{row[4]}

Filename:
{row[1]}

Chunk:
{row[3]}

Similarity Score:
{similarity:.3f}

Match Type:
SEMANTIC SEARCH

Content:

{row[2]}

==================================================
"""
        )

    ###########################################################
    # 6. LOG RESULT
    ###########################################################

    print("=" * 70)
    print("SEMANTIC RETRIEVAL RESULT")
    print(f"Best Score    : {best_score:.3f}")
    print(f"Chunks        : {len(contexts)}")
    print(f"Comprehensive : {is_comprehensive}")
    print("=" * 70)

    return {
        "contexts": contexts,
        "match_type": "semantic",
        "best_score": round(
            best_score,
            3,
        ),
    }

@router.post("/ask-bir")
def ask_bir(
    request,
    data: Form[PromptInput],
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

    agent_id = data.agent

    try:

        history = json.loads(data.history)

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
                WHERE id = %s
                """,
                (agent_id,),
            )

            row = cursor.fetchone()

            if not row:

                return {
                    "response": "Invalid BIR agent.",
                    "match_type": "none",
                    "score": 0,
                    "documents": 0,
                }

            agent_name = row[0]

    except Exception as e:

        print(
            f"Agent lookup error: {str(e)}"
        )

        return {
            "response": (
                "Unable to determine the selected "
                "BIR agent."
            ),
            "match_type": "none",
            "score": 0,
            "documents": 0,
        }

    ###########################################################
    # 4. GENERATE QUERY EMBEDDING
    ###########################################################

    try:

        query_embedding = get_embedding(prompt)

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
            f"Embedding error: {str(e)}"
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
    # 5. DOCUMENT REFERENCE DETECTION
    ###########################################################

    document = extract_document_reference(
        prompt
    )

    if document:

        print("=" * 70)
        print("DOCUMENT DETECTED")
        print(document)
        print("=" * 70)

    ###########################################################
    # 6. RETRIEVE FROM POSTGRESQL + PGVECTOR
    ###########################################################

    try:

        retrieval = search_bir_knowledge_base(
            query_embedding=query_embedding,
            agent_name=agent_name,
            user_question=prompt,
            document=document,
            limit=5,
        )

    except Exception as e:

        print("=" * 70)
        print("BIR RETRIEVAL ERROR")
        print(str(e))
        print("=" * 70)

        retrieval = {
            "contexts": [],
            "match_type": "none",
            "best_score": 0,
        }

    ###########################################################
    # 7. PREPARE CONTEXT
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

    no_docs_found = not contexts

    if contexts:

        context = "\n\n".join(
            contexts
        )

    else:

        context = (
            "No relevant documents were found "
            "in the Internal Knowledge Base."
        )

    ###########################################################
    # 8. DEBUG INFORMATION
    ###########################################################

    print("=" * 70)
    print("BIR RETRIEVAL RESULT")
    print(f"Agent      : {agent_name}")
    print(f"Question   : {prompt}")
    print(f"Match Type : {match_type}")
    print(f"Best Score : {best_score}")
    print(f"Documents  : {len(contexts)}")
    print("=" * 70)

    ###########################################################
    # 9. GPT
    ###########################################################

    try:

        if no_docs_found:

            response = bir_openai_gpt45(
                prompt=prompt,
                history=history,
                from_ask_bir=True,
            )

        else:

            response = openai_gpt45(
                prompt=prompt,
                context=context,
                history=history,
                match_type=match_type,
                best_score=best_score,
            )

    except Exception as e:

        print(
            f"GPT ERROR: {str(e)}"
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
    # 10. RETURN
    ###########################################################

    return {

        "response": response,

        "match_type": match_type,

        "score": best_score,

        "documents": len(contexts),

    }


# ==========================================
# 3. UNIFIED KX INGESTION ENDPOINT
# ==========================================
@router.post("/ingest-knowledge")
def ingest_knowledge(
    request, 
    file: UploadedFile = File(...),
    title: str = Form(...),
    agent: str = Form(...),
    uploaded_by: str = Form("Admin")
):
    conn = None

    try:

        ###########################################################
        # 1. READ FILE
        ###########################################################

        file_bytes = file.read()

        if not file_bytes:
            return {
                "status": "error",
                "message": "Uploaded file is empty."
            }

        print("=" * 70)
        print("BIR KNOWLEDGE INGESTION")
        print(f"File: {file.name}")
        print(f"Size: {len(file_bytes):,} bytes")
        print("=" * 70)

        ###########################################################
        # 2. EXTRACT TEXT
        ###########################################################

        print("Extracting text...")

        text_content = extract_text(
            file.name,
            file_bytes
        )

        if not text_content:

            return {
                "status": "error",
                "message": "Text extraction failed."
            }

        clean_content = (
            text_content
            .replace("\x00", "")
            .strip()
        )

        if not clean_content:

            return {
                "status": "error",
                "message": "No text could be extracted from the file."
            }
            

        ###########################################################
        # 3. CHUNK DOCUMENT
        ###########################################################

        chunks = chunk_text(
            clean_content,
            chunk_size=800,
            overlap=150
        )

        valid_chunks = []

        for idx, chunk in enumerate(chunks):

            chunk = chunk.strip()

            if len(chunk) < 30:
                continue

            valid_chunks.append(
                (idx, chunk)
            )

        print(
            f"Extracted {len(valid_chunks)} valid chunks."
        )

        if not valid_chunks:

            return {
                "status": "error",
                "message": "No valid document chunks were generated."
            }
        ###########################################################
        # 4. UPLOAD TO IPFS
        ###########################################################

        print("Uploading file to IPFS...")

        file_cid = upload_to_ipfs(
            file_name=file.name,
            file_bytes=file_bytes,
            content_type=(
                file.content_type
                or "application/json"
            ),
        )

        print(f"IPFS CID: {file_cid}")

        ###########################################################
        # 5. DATABASE
        ###########################################################

        conn = connections["birai_db"]

        print("=" * 70)
        print("DATABASE")
        print(
            f"Target DB: "
            f"{conn.settings_dict.get('NAME')} "
            f"@ "
            f"{conn.settings_dict.get('HOST')}"
        )
        print("=" * 70)

        ###########################################################
        # 6. TRANSACTION
        ###########################################################

        with transaction.atomic(
            using="birai_db"
        ):

            with conn.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT agent
                    FROM kx_agents
                    WHERE id = %s
                    """,
                    (agent,),
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

                title = (
                    title.strip()
                    if title
                    else os.path.splitext(file.name)[0]
                )
        
                print("Creating KX topic...")
        
                with connections["birai_db"].cursor() as cursor:
        
                    cursor.execute(
                        """
                        INSERT INTO kx_topics (
                            topic_title,
                            agent,
                            file_name,
                            uploaded_by,
                            agent_id,
                            file_cid
                        )
                        VALUES (
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
                        ],
                    )
        
                    topic_id = cursor.fetchone()[0]
        
                    print(
                        f"Master Record Created: "
                        f"ID {topic_id}"
                    )

                    ###################################################
                    # GENERATE + INSERT EMBEDDINGS
                    ###################################################

                    inserted_chunks = 0

                    print(
                        "Generating embeddings..."
                    )

                    for chunk_index, chunk in valid_chunks:

                        print(
                            f"Embedding chunk "
                            f"{inserted_chunks + 1}/"
                            f"{len(valid_chunks)}..."
                        )

                        ################################################
                        # Enriched embedding text
                        ################################################

                        enriched_chunk = (
                            f"Document: {title}. "
                            f"Bureau of Internal Revenue "
                            f"Philippines. "
                            f"Section Content:\n"
                            f"{chunk}"
                        )

                        ################################################
                        # Generate embedding
                        ################################################

                        vector = get_embedding(
                            enriched_chunk
                        )

                        ################################################
                        # Validate embedding
                        ################################################

                        if not vector:

                            print(
                                f"WARNING: Empty embedding "
                                f"for chunk {chunk_index}"
                            )

                            continue

                        vector = [
                            float(value)
                            for value in vector
                        ]

                        ################################################
                        # Verify dimensions
                        ################################################

                        if len(vector) != 1536:

                            raise ValueError(
                                f"Invalid embedding dimension "
                                f"for chunk {chunk_index}: "
                                f"expected 1536, "
                                f"got {len(vector)}"
                            )

                        ################################################
                        # Insert chunk into PostgreSQL
                        ################################################

                        cursor.execute(
                            """
                            INSERT INTO bir_document (
                                kx_topics_id,
                                filename,
                                content,
                                chunk_index,
                                embedding,
                                chunk_length
                            )
                            VALUES (
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
                                file.name,
                                chunk,
                                chunk_index,
                                vector,
                                len(chunk),
                            ],
                        )

                        inserted_chunks += 1

                ###################################################
                # Verify
                ###################################################

                print(
                    f"Inserted {inserted_chunks} "
                    f"chunks into PostgreSQL."
                )

        ###########################################################
        # 7. SUCCESS
        ###########################################################

        print("=" * 70)
        print("TRANSACTION COMMITTED")
        print(
            f"Topic ID : {topic_id}"
        )
        print(
            f"Chunks   : {inserted_chunks}"
        )
        print(
            f"IPFS CID : {file_cid}"
        )
        print("=" * 70)

        return {
            "status": "success",
            "topic_id": topic_id,
            "chunks": inserted_chunks,
            "file_cid": file_cid,
        }

    ###############################################################
    # 8. ERROR HANDLING
    ###############################################################

    except Exception as e:

        print("=" * 70)
        print("!!! BIR INGESTION ERROR !!!")
        print(str(e))
        print("=" * 70)

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        return {
            "status": "error",
            "message": str(e)
        }