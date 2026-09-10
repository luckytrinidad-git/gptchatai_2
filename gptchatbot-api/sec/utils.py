from django.db import connections

import re

VECTOR_THRESHOLD = 0.30
TEXT_THRESHOLD = 0.01
    
def get_sec_query_metadata():
    """
    Get known SEC metadata values from kx_topics.

    Used by extract_sec_query_filters() to determine
    which company / SEC number / form type exists in
    the knowledge base.
    """

    with connections["birai_db"].cursor() as cursor:

        cursor.execute(
            """
            SELECT DISTINCT
                company_name,
                sec_no,
                form_type
            FROM kx_topics
            WHERE
                company_name IS NOT NULL
                OR sec_no IS NOT NULL
                OR form_type IS NOT NULL
            """
        )

        rows = cursor.fetchall()

    companies = set()
    sec_numbers = set()
    form_types = set()

    for company_name, sec_no, form_type in rows:

        if company_name:
            companies.add(
                company_name.strip()
            )

        if sec_no:
            sec_numbers.add(
                sec_no.strip()
            )

        if form_type:
            form_types.add(
                form_type.strip()
            )

    return {
        "companies": sorted(companies),
        "sec_numbers": sorted(sec_numbers),
        "form_types": sorted(form_types),
    }

def extract_sec_query_filters(
    prompt,
    known_companies=None,
    known_sec_numbers=None,
    known_form_types=None,
):
    """
    Extract SEC-related filters from a natural-language question.

    Returns:

    {
        "company_name": ...,
        "sec_no": ...,
        "form_type": ...,
        "period_covered": ...,
        "document": ...
    }

    All values may be None when they cannot be detected.
    """

    if not prompt:
        return {
            "company_name": None,
            "sec_no": None,
            "form_type": None,
            "period_covered": None,
            "document": None,
        }

    prompt = prompt.strip()

    result = {
        "company_name": None,
        "sec_no": None,
        "form_type": None,
        "period_covered": None,
        "document": None,
    }

    # =========================================================
    # 1. COMPANY
    # =========================================================

    if known_companies:

        prompt_lower = prompt.lower()

        # Longest first so that:
        #
        # "ABC Corporation Holdings"
        #
        # is checked before:
        #
        # "ABC Corporation"
        #

        companies = sorted(
            known_companies,
            key=lambda value: len(str(value)),
            reverse=True,
        )

        for company in companies:

            if not company:
                continue

            company_text = str(company).strip()

            if not company_text:
                continue

            if company_text.lower() in prompt_lower:

                result["company_name"] = company_text

                break

    # =========================================================
    # 2. SEC NUMBER
    # =========================================================

    if known_sec_numbers:

        prompt_lower = prompt.lower()

        for sec_number in known_sec_numbers:

            if not sec_number:
                continue

            sec_number = str(sec_number).strip()

            if not sec_number:
                continue

            if sec_number.lower() in prompt_lower:

                result["sec_no"] = sec_number

                break

    # =========================================================
    # 3. FORM TYPE
    # =========================================================

    if known_form_types:

        prompt_lower = prompt.lower()

        form_types = sorted(
            known_form_types,
            key=lambda value: len(str(value)),
            reverse=True,
        )

        for form_type in form_types:

            if not form_type:
                continue

            form_text = str(form_type).strip()

            if not form_text:
                continue

            if form_text.lower() in prompt_lower:

                result["form_type"] = form_text

                break

    # =========================================================
    # 4. PERIOD / YEAR
    # =========================================================

    # Detect years such as:
    #
    # 2024
    # 2025
    # 2026
    #

    years = re.findall(
        r"\b(19\d{2}|20\d{2})\b",
        prompt,
    )

    if years:

        # Use the first year for now.
        #
        # Later, when we implement comparison/table
        # questions, we will intentionally support multiple
        # periods.

        result["period_covered"] = years[0]

    # =========================================================
    # 5. DOCUMENT REFERENCE
    # =========================================================

    document_patterns = [

        r"\bannual report\b",

        r"\bannual filing\b",

        r"\bfinancial statement[s]?\b",

        r"\bfinancial report[s]?\b",

        r"\bquarterly report\b",

        r"\bquarterly filing\b",

        r"\b17[- ]a\b",

        r"\b17[- ]q\b",

        r"\b17[- ]c\b",

        r"\b17[- ]i\b",

        r"\bpublic offering\b",

        r"\bregistration statement\b",

    ]

    prompt_lower = prompt.lower()

    for pattern in document_patterns:

        match = re.search(
            pattern,
            prompt_lower,
        )

        if match:

            result["document"] = (
                match.group(0)
            )

            break

    return result

def search_sec_knowledge_base(
    query_embedding,
    user_question=None,
    company_name=None,
    sec_no=None,
    form_type=None,
    period_covered=None,
    document=None,
    limit=5,
):
    """
    Hybrid SEC knowledge-base retrieval.

    Retrieval flow:

        User question
             ↓
        Metadata filters
             ↓
        ┌───────────────────┐
        │                   │
        ↓                   ↓
    Vector search       FTS search
      top N               top N
        │                   │
        └─────────┬─────────┘
                  ↓
          Merge / deduplicate
                  ↓
           Hybrid scoring
                  ↓
             Top `limit`

    Vector retrieval is semantic.
    FTS retrieval is keyword-based.

    A document can enter the candidate pool through either
    retrieval method.
    """

    # ---------------------------------------------------------
    # Normalize inputs
    # ---------------------------------------------------------

    user_question = (user_question or "").strip()
    company_name = (company_name or "").strip()
    sec_no = (sec_no or "").strip()
    form_type = (form_type or "").strip()
    period_covered = (period_covered or "").strip()
    document = (document or "").strip()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5

    if limit <= 0:
        limit = 5

    # Retrieve more candidates independently before final ranking.
    #
    # Example:
    # limit = 5
    # candidate_limit = 15
    #
    # This gives vector search 15 opportunities and FTS 15
    # opportunities before we reduce the result to the final 5.
    candidate_limit = max(limit * 3, 10)

    # ---------------------------------------------------------
    # Validate / normalize embedding
    # ---------------------------------------------------------

    if not query_embedding:
        return {
            "contexts": [],
            "results": [],
            "match_type": "none",
            "best_score": 0.0,
        }

    # Some embedding helpers return:
    #
    # [
    #     [0.1, 0.2, ...]
    # ]
    #
    # while this function expects:
    #
    # [
    #     0.1, 0.2, ...
    # ]
    #
    # Unwrap a single nested embedding.

    if (
        isinstance(query_embedding, (list, tuple))
        and len(query_embedding) == 1
        and isinstance(query_embedding[0], (list, tuple))
    ):
        query_embedding = query_embedding[0]

    try:
        query_embedding = [
            float(value)
            for value in query_embedding
        ]

    except (TypeError, ValueError) as exc:

        print("=" * 70)
        print("SEC EMBEDDING NORMALIZATION ERROR")
        print("Embedding type :", type(query_embedding))
        print("Error          :", str(exc))
        print("=" * 70)

        return {
            "contexts": [],
            "results": [],
            "match_type": "none",
            "best_score": 0.0,
        }

    if len(query_embedding) != 1536:

        raise ValueError(
            f"Expected 1536-dimensional embedding, "
            f"got {len(query_embedding)} dimensions."
        )

    # PostgreSQL / pgvector accepts this format for vector casting.
    embedding_string = (
        "["
        + ",".join(
            str(value)
            for value in query_embedding
        )
        + "]"
    )

    # ---------------------------------------------------------
    # Build metadata filters
    # ---------------------------------------------------------

    metadata_conditions = []
    metadata_params = []

    if company_name:
        metadata_conditions.append(
            "LOWER(t.company_name) = LOWER(%s)"
        )
        metadata_params.append(company_name)

    if sec_no:
        metadata_conditions.append(
            "LOWER(t.sec_no) = LOWER(%s)"
        )
        metadata_params.append(sec_no)

    if form_type:
        metadata_conditions.append(
            "LOWER(t.form_type) = LOWER(%s)"
        )
        metadata_params.append(form_type)

    if period_covered:
        metadata_conditions.append(
            "LOWER(t.period_covered) = LOWER(%s)"
        )
        metadata_params.append(period_covered)

    if document:
        metadata_conditions.append(
            """
            (
                LOWER(t.topic_title) LIKE LOWER(%s)
                OR LOWER(t.file_name) LIKE LOWER(%s)
            )
            """
        )

        document_pattern = f"%{document}%"

        metadata_params.extend([
            document_pattern,
            document_pattern,
        ])

    # Always have a valid WHERE clause.
    metadata_where = " AND ".join(
        metadata_conditions
    )

    if metadata_where:
        metadata_where = "WHERE " + metadata_where

    # ---------------------------------------------------------
    # PostgreSQL query
    # ---------------------------------------------------------
    #
    # base_documents
    #     Applies metadata filtering once.
    #
    # vector_candidates
    #     Gets the best semantic matches independently.
    #
    # fts_candidates
    #     Gets the best keyword matches independently.
    #
    # candidate_pool
    #     Combines both result sets.
    #
    # candidate_scores
    #     Deduplicates documents that appeared in both.
    #
    # final ranking
    #     Applies the hybrid score.
    #
    # ---------------------------------------------------------

    sql = f"""
        WITH base_documents AS (
            SELECT
                d.id,
                d.content,
                d.page_number,
                d.section_name,
                d.chunk_index,
                d.metadata,
                d.embedding,
                d.search_vector,

                t.id AS kx_topic_id,
                t.topic_title,
                t.file_name,
                t.company_name,
                t.sec_no,
                t.form_type,
                t.period_covered

            FROM sec_document d

            INNER JOIN kx_topics t
                ON t.id = d.kx_topic_id

            {metadata_where}

            AND d.embedding IS NOT NULL
        ),

        vector_candidates AS (
            SELECT
                bd.*,

                1 - (
                    bd.embedding <=> %s::vector
                ) AS vector_score,

                NULL::double precision AS text_score

            FROM base_documents bd

            ORDER BY
                bd.embedding <=> %s::vector

            LIMIT %s
        ),

        fts_candidates AS (
            SELECT
                bd.*,

                NULL::double precision AS vector_score,

                ts_rank_cd(
                    bd.search_vector,
                    plainto_tsquery(
                        'english',
                        %s
                    )
                ) AS text_score

            FROM base_documents bd

            WHERE
                bd.search_vector IS NOT NULL
                AND bd.search_vector @@ plainto_tsquery(
                    'english',
                    %s
                )

            ORDER BY
                ts_rank_cd(
                    bd.search_vector,
                    plainto_tsquery(
                        'english',
                        %s
                    )
                ) DESC

            LIMIT %s
        ),

        candidate_pool AS (
            SELECT
                *
            FROM vector_candidates

            UNION ALL

            SELECT
                *
            FROM fts_candidates
        ),

        candidate_scores AS (
            SELECT
                id,

                MAX(content) AS content,
                MAX(page_number) AS page_number,
                MAX(section_name) AS section_name,
                MAX(chunk_index) AS chunk_index,
                MAX(metadata::text)::jsonb AS metadata,

                MAX(kx_topic_id) AS kx_topic_id,
                MAX(topic_title) AS topic_title,
                MAX(file_name) AS file_name,
                MAX(company_name) AS company_name,
                MAX(sec_no) AS sec_no,
                MAX(form_type) AS form_type,
                MAX(period_covered) AS period_covered,

                MAX(vector_score) AS vector_score,
                MAX(text_score) AS text_score

            FROM candidate_pool

            GROUP BY id
        )

        SELECT
            id,

            content,
            page_number,
            section_name,
            chunk_index,
            metadata,

            kx_topic_id,
            topic_title,
            file_name,
            company_name,
            sec_no,
            form_type,
            period_covered,

            COALESCE(vector_score, 0.0)
                AS vector_score,

            COALESCE(text_score, 0.0)
                AS text_score,

            (
                COALESCE(vector_score, 0.0) * 0.75
                +
                LEAST(
                    COALESCE(text_score, 0.0),
                    1.0
                ) * 0.25
            ) AS combined_score

        FROM candidate_scores

        ORDER BY
            combined_score DESC

        LIMIT %s
    """

    # ---------------------------------------------------------
    # Query parameters
    # ---------------------------------------------------------
    #
    # base_documents metadata parameters appear first because
    # the metadata WHERE clause is physically inside the first
    # CTE.
    #
    # Then:
    #
    # vector embedding
    # vector embedding
    # vector candidate limit
    #
    # FTS question
    # FTS question
    # FTS question
    # FTS candidate limit
    #
    # final limit
    # ---------------------------------------------------------

    params = []

    params.extend(metadata_params)

    params.extend([
        embedding_string,
        embedding_string,
        candidate_limit,

        user_question,
        user_question,
        user_question,
        candidate_limit,

        limit,
    ])

    # ---------------------------------------------------------
    # Execute query
    # ---------------------------------------------------------

    conn = connections["birai_db"]

    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

            print("=" * 70)
            print("SEC KNOWLEDGE SEARCH DEBUG")
            print("company_name   :", company_name)
            print("sec_no         :", sec_no)
            print("form_type      :", form_type)
            print("period_covered :", period_covered)
            print("document       :", document)
            print("candidate_limit:", candidate_limit)
            print("final_limit    :", limit)
            print("rows reFturned  :", len(rows))
            print("=" * 70)
            
            columns = [
                column[0]
                for column in cursor.description
            ]

    except Exception as exc:
        print(
            "SEC KNOWLEDGE SEARCH ERROR:",
            str(exc),
        )

        raise

    # ---------------------------------------------------------
    # Convert rows into dictionaries
    # ---------------------------------------------------------

    results = []

    for row in rows:
        item = dict(
            zip(columns, row)
        )

        # Normalize metadata.
        metadata = item.get("metadata")

        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        if metadata is None:
            metadata = {}

        item["metadata"] = metadata

        # Normalize scores.
        item["vector_score"] = float(
            item.get("vector_score") or 0.0
        )

        item["text_score"] = float(
            item.get("text_score") or 0.0
        )

        item["combined_score"] = float(
            item.get("combined_score") or 0.0
        )

        results.append(item)

    # ---------------------------------------------------------
    # Determine match type
    # ---------------------------------------------------------

    has_vector_match = any(
        result["vector_score"] > 0
        for result in results
    )

    has_text_match = any(
        result["text_score"] > 0
        for result in results
    )

    if has_vector_match and has_text_match:
        match_type = "hybrid"

    elif has_vector_match:
        match_type = "vector"

    elif has_text_match:
        match_type = "keyword"

    else:
        match_type = "none"

    # ---------------------------------------------------------
    # Best score
    # ---------------------------------------------------------

    best_score = (
        results[0]["combined_score"]
        if results
        else 0.0
    )

    # ---------------------------------------------------------
    # Build retrieval contexts
    # ---------------------------------------------------------

    contexts = []

    for result in results:
        context = {
            "id": result["id"],
            "kx_topic_id": result["kx_topic_id"],
            "content": result["content"],
            "page_number": result["page_number"],
            "section_name": result["section_name"],
            "chunk_index": result["chunk_index"],
            "metadata": result["metadata"],

            "topic_title": result["topic_title"],
            "file_name": result["file_name"],
            "company_name": result["company_name"],
            "sec_no": result["sec_no"],
            "form_type": result["form_type"],
            "period_covered": result["period_covered"],

            "vector_score": result["vector_score"],
            "text_score": result["text_score"],
            "combined_score": result["combined_score"],
        }

        contexts.append(context)

    # ---------------------------------------------------------
    # Return
    # ---------------------------------------------------------

    return {
        "contexts": contexts,
        "results": results,
        "match_type": match_type,
        "best_score": best_score,
    }