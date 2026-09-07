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
    Search SEC knowledge using:

    1. Metadata filtering through kx_topics
    2. PostgreSQL full-text search
    3. pgvector similarity search

    kx_topics contains the authoritative submission metadata:
        company_name
        sec_no
        form_type
        period_covered
        topic_title
        file_name

    sec_document contains:
        content
        page_number
        section_name
        chunk_index
        metadata
        embedding
        search_vector
    """

    if not query_embedding:
        return {
            "contexts": [],
            "results": [],
            "match_type": "none",
            "best_score": 0,
        }

    # =========================================================
    # NORMALIZE PARAMETERS
    # =========================================================

    company_name = (
        company_name.strip()
        if company_name
        else None
    )

    sec_no = (
        sec_no.strip()
        if sec_no
        else None
    )

    form_type = (
        form_type.strip()
        if form_type
        else None
    )

    period_covered = (
        period_covered.strip()
        if period_covered
        else None
    )

    document = (
        document.strip()
        if document
        else None
    )

    user_question = (
        user_question.strip()
        if user_question
        else ""
    )

    # =========================================================
    # VECTOR
    # =========================================================

    vector = [
        float(value)
        for value in query_embedding
    ]

    if len(vector) != 1536:
        raise ValueError(
            "Invalid query embedding dimension. "
            f"Expected 1536, got {len(vector)}."
        )

    # =========================================================
    # BUILD METADATA FILTERS
    # =========================================================

    filters = []

    params = []

    # ---------------------------------------------------------
    # Company
    # ---------------------------------------------------------

    if company_name:

        filters.append(
            """
            LOWER(t.company_name)
            = LOWER(%s)
            """
        )

        params.append(company_name)

    # ---------------------------------------------------------
    # SEC number
    # ---------------------------------------------------------

    if sec_no:

        filters.append(
            """
            LOWER(t.sec_no)
            = LOWER(%s)
            """
        )

        params.append(sec_no)

    # ---------------------------------------------------------
    # Form type
    # ---------------------------------------------------------

    if form_type:

        filters.append(
            """
            LOWER(t.form_type)
            = LOWER(%s)
            """
        )

        params.append(form_type)

    # ---------------------------------------------------------
    # Period covered
    # ---------------------------------------------------------

    if period_covered:

        filters.append(
            """
            LOWER(t.period_covered)
            = LOWER(%s)
            """
        )

        params.append(period_covered)

    # ---------------------------------------------------------
    # Document reference
    #
    # Search against both title and filename.
    # ---------------------------------------------------------

    if document:

        filters.append(
            """
            (
                LOWER(t.topic_title) LIKE LOWER(%s)
                OR
                LOWER(t.file_name) LIKE LOWER(%s)
            )
            """
        )

        document_pattern = f"%{document}%"

        params.extend([
            document_pattern,
            document_pattern,
        ])

    # =========================================================
    # WHERE CLAUSE
    # =========================================================

    filters.append(
        "d.embedding IS NOT NULL"
    )

    where_clause = (
        "WHERE "
        + " AND ".join(filters)
    )

    # =========================================================
    # FULL-TEXT SEARCH
    # =========================================================

    # plainto_tsquery is safer than directly inserting
    # user input into tsquery syntax.

    fts_expression = """
        plainto_tsquery(
            'english',
            %s
        )
    """

    # =========================================================
    # SQL
    # =========================================================

    sql = f"""
        WITH ranked_documents AS (

            SELECT

                d.id,
                d.kx_topic_id,
                d.content,
                d.page_number,
                d.section_name,
                d.chunk_index,
                d.metadata,

                t.topic_title,
                t.file_name,
                t.company_name,
                t.sec_no,
                t.form_type,
                t.period_covered,

                ------------------------------------------------
                -- VECTOR DISTANCE
                ------------------------------------------------

                d.embedding <=> %s::vector
                    AS vector_distance,

                ------------------------------------------------
                -- VECTOR SIMILARITY
                --
                -- cosine distance:
                --
                -- 0 = identical
                -- 1 = very different
                --
                -- Convert it to similarity.
                ------------------------------------------------

                (
                    1 - (
                        d.embedding <=> %s::vector
                    )
                ) AS vector_score,

                ------------------------------------------------
                -- FULL TEXT SCORE
                ------------------------------------------------

                ts_rank_cd(
                    d.search_vector,
                    {fts_expression}
                ) AS text_score

            FROM sec_document d

            INNER JOIN kx_topics t
                ON t.id = d.kx_topic_id

            {where_clause}

        )

        SELECT *

        FROM ranked_documents

        ORDER BY

            (
                (vector_score * 0.75)
                +
                (
                    LEAST(
                        text_score,
                        1.0
                    ) * 0.25
                )
            ) DESC

        LIMIT %s
    """

    # =========================================================
    # PARAMETERS
    # =========================================================

    sql_params = [

        # vector_score
        vector,

        # vector_score again
        vector,

        # FTS query
        user_question,

        # metadata filters
        *params,

        # limit
        limit,
    ]

    # =========================================================
    # EXECUTE
    # =========================================================

    with connections["birai_db"].cursor() as cursor:

        cursor.execute(
            sql,
            sql_params
        )

        columns = [
            column[0]
            for column in cursor.description
        ]

        rows = cursor.fetchall()

    # =========================================================
    # NO RESULTS
    # =========================================================

    if not rows:

        return {
            "contexts": [],
            "results": [],
            "match_type": "none",
            "best_score": 0,
        }

    # =========================================================
    # PROCESS RESULTS
    # =========================================================

    results = []

    contexts = []

    for row in rows:

        record = dict(
            zip(
                columns,
                row
            )
        )

        vector_score = (
            float(
                record.get(
                    "vector_score",
                    0
                )
                or 0
            )
        )

        text_score = (
            float(
                record.get(
                    "text_score",
                    0
                )
                or 0
            )
        )

        combined_score = (
            (vector_score * 0.75)
            +
            (
                min(text_score, 1.0)
                * 0.25
            )
        )

        # -----------------------------------------------------
        # Add combined score
        # -----------------------------------------------------

        record["combined_score"] = combined_score

        # -----------------------------------------------------
        # Build context
        # -----------------------------------------------------

        context = (
            f"Company: "
            f"{record.get('company_name') or 'Unknown'}\n"

            f"SEC No.: "
            f"{record.get('sec_no') or 'Unknown'}\n"

            f"Form Type: "
            f"{record.get('form_type') or 'Unknown'}\n"

            f"Period Covered: "
            f"{record.get('period_covered') or 'Unknown'}\n"

            f"Document: "
            f"{record.get('topic_title') or record.get('file_name')}\n"

            f"File: "
            f"{record.get('file_name') or 'Unknown'}\n"

            f"Page: "
            f"{record.get('page_number') or 'Unknown'}\n"

            f"Section: "
            f"{record.get('section_name') or 'Unknown'}\n"

            f"Content:\n"
            f"{record.get('content') or ''}"
        )

        record["context"] = context

        contexts.append(context)

        results.append(record)

    # =========================================================
    # BEST SCORE
    # =========================================================

    best_score = max(
        record["combined_score"]
        for record in results
    )

    # =========================================================
    # MATCH TYPE
    # =========================================================

    has_vector = any(
        record["vector_score"] >= VECTOR_THRESHOLD
        for record in results
    )

    has_text = any(
        record["text_score"] >= TEXT_THRESHOLD
        for record in results
    )

    if has_vector and has_text:

        match_type = "hybrid"

    elif has_vector:

        match_type = "vector"

    elif has_text:

        match_type = "keyword"

    else:

        match_type = "none"

    # =========================================================
    # RETURN
    # =========================================================

    return {
        "contexts": contexts,
        "results": results,
        "match_type": match_type,
        "best_score": best_score,
    }