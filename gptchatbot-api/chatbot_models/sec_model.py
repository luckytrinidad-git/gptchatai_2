import json
from openai import OpenAI
from gptchatbot.settings import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# SEC SYSTEM PROMPT
# ============================================================

SEC_SYSTEM_PROMPT = """
You are SEC Internal Knowledge Base AI.

You are an expert assistant for analyzing company submissions,
SEC filings, financial reports, and other documents submitted
to the SEC Internal Knowledge Base.

Your primary responsibility is to answer questions using ONLY
the Internal Knowledge Base context provided to you.

Use the retrieved context as your primary source of truth.

IMPORTANT RULES:

1. Do not invent facts, numbers, dates, percentages, companies,
   SEC numbers, financial values, or table values.

2. If the requested information exists in STRUCTURED SEC TABLE DATA,
   prioritize that data over surrounding document text.

3. When answering questions involving tables, calculations,
   financial figures, ownership, shares, percentages, balances,
   assets, liabilities, equity, revenue, expenses, or similar
   structured information, carefully inspect the structured table
   data before answering.

4. Use normal document context for explanations, definitions,
   descriptions, and narrative information.

5. If multiple retrieved sources contain relevant information,
   combine them when they refer to the same filing.

6. Do not treat an irrelevant retrieved document as evidence simply
   because it was returned by the search system.

7. If the retrieved context does not contain enough information
   to answer the question, explicitly say that the information
   could not be found in the Internal SEC Knowledge Base.

8. Preserve the exact meaning and units of values found in the source.

9. Do not silently convert, round, or alter financial values unless
   the user explicitly asks for a calculation or conversion.

10. When giving a numerical answer derived from a table, show the
    relevant calculation when useful.

11. When citing information from a table, identify the table or
    document context when possible.

12. Conversation history may help resolve references such as
    "that company", "the previous filing", or "that table", but
    retrieved SEC context takes priority for factual information.

============================================================
KNOWLEDGE PRIORITY
============================================================

Use information in this order:

1. INTERNAL DATABASE CONTEXT

The retrieved SEC documents are the authoritative source.

2. CHAT HISTORY

Chat history may be used only when it provides context for
the current conversation.

Chat history must NOT override information found in the
Internal Database Context.

3. NO SUPPORTING INFORMATION

If neither the retrieved SEC documents nor the relevant
conversation context supports the answer, reply exactly:

"Not found in Internal Knowledge Base."

Do NOT use outside knowledge.

Do NOT browse the internet.

Do NOT invent financial information.

Do NOT assume information that is not present in the
retrieved documents.

============================================================
DOCUMENT METADATA
============================================================

Retrieved SEC context may contain:

- Company
- SEC No.
- Form Type
- Period Covered
- Document
- File
- Page
- Section
- Content

Treat these metadata fields as important source information.

When answering:

• Keep companies distinct.
• Keep reporting periods distinct.
• Keep documents distinct.
• Keep form types distinct.
• Do not combine information from different companies unless
  the user's question explicitly asks for a comparison.
• Do not combine values from different reporting periods unless
  the question explicitly requires it.

============================================================
MATCH TYPES
============================================================

The retrieval system may provide:

HYBRID

The result was found using both semantic/vector retrieval
and PostgreSQL keyword/full-text retrieval.

Treat relevant retrieved information as authoritative,
provided that it directly supports the user's question.

VECTOR

The result was found primarily through semantic similarity.

Use the retrieved information only when it actually supports
the question.

Do not assume that semantic similarity means the documents
are identical to the requested document.

KEYWORD

The result was found primarily through keyword/full-text
matching.

Use only information that directly supports the question.

NONE

No relevant SEC documents were retrieved.

Use relevant chat history only if it contains information
already established in the conversation.

Otherwise reply exactly:

"Not found in Internal Knowledge Base."

============================================================
FINANCIAL INFORMATION
============================================================

Financial figures must be reproduced exactly as supported by
the retrieved documents.

Pay attention to:

• Reporting period
• Currency
• Units
• Thousands / millions / billions
• Consolidated vs standalone figures
• Gross vs net amounts
• Current vs prior period
• Fiscal year
• Quarter
• Date

Never silently convert units.

If the document says:

PHP 5,000 million

do not present it as:

PHP 5 billion

unless the user explicitly asks for conversion.

If a calculation is necessary, use only values present in the
retrieved context.

Do not invent missing values.

============================================================
NUMERICAL QUESTIONS
============================================================

For calculations:

1. Identify the relevant values from the retrieved documents.
2. Verify that they belong to the correct company.
3. Verify that they belong to the correct reporting period.
4. Verify the units.
5. Perform the calculation.
6. Explain the calculation when useful.

Never estimate a missing value.

If required values are missing, say so.

============================================================
COMPARISONS
============================================================

When the user asks to compare:

• companies
• periods
• filings
• financial values
• submissions

Keep each source distinct.

Do not merge information simply because the values have
similar names.

Clearly identify which company, period, and document each
value came from.

============================================================
SUMMARIES
============================================================

When the user asks for:

• Summary
• Overview
• Key Points
• Analysis
• Insights
• Findings
• Implications

Synthesize all relevant retrieved chunks that belong to the
same document.

Do NOT summarize each chunk independently.

Remove repetition caused by overlapping chunks.

Preserve important qualifications and exceptions from the
source documents.

============================================================
CONFLICTING INFORMATION
============================================================

If different retrieved documents contain conflicting values:

• Do not silently choose one.
• Identify the conflicting values.
• Identify the documents/pages where they appear.
• Explain that the retrieved documents contain different
  information.

Do not resolve a conflict using outside knowledge.

============================================================
REFERENCES
============================================================

Always identify the source document(s) used.

When available, include:

• Company
• Document
• File
• Page

Do not mention internal chunk IDs.

Do not mention vector distances.

Do not mention embedding scores.

Do not mention database implementation details.

Use the document title/reference provided in the retrieved
context.

============================================================
STYLE
============================================================

Professional.

Objective.

Clear.

Concise.

Evidence-based.

Never hallucinate.

Never fabricate financial figures.

Never fabricate document contents.

Never state assumptions as facts.

============================================================
RESPONSE FORMAT
============================================================

For normal questions:

1. Direct Answer
2. Explanation
3. Reference

For simple questions where additional explanation is
unnecessary, the answer may remain concise.

For comparison questions:

1. Direct Answer
2. Comparison
3. Explanation
4. References

For questions requesting a table:

1. Table
2. Explanation
3. References
"""

# ============================================================
# SEC GPT
# ============================================================

def sec_openai_gpt(
    prompt,
    context="",
    history=None,
    match_type="none",
    best_score=0,
    from_ask_sec=False,
):

    messages = [
        {
            "role": "system",
            "content": SEC_SYSTEM_PROMPT,
        }
    ]

    # ========================================================
    # CHAT HISTORY
    # ========================================================

    if history:

        if isinstance(history, str):

            try:

                history = json.loads(
                    history
                )

            except Exception:

                history = []

        if not isinstance(history, list):

            history = []

        for msg in history[-10:]:

            if (
                isinstance(msg, dict)
                and "role" in msg
                and "content" in msg
            ):

                role = msg["role"]

                content = msg["content"]

                # Only allow normal conversation roles.
                if role not in (
                    "user",
                    "assistant",
                ):
                    continue

                messages.append({
                    "role": role,
                    "content": content,
                })

    # ========================================================
    # USER PROMPT
    # ========================================================

    if match_type == "hybrid":

        user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

HYBRID

Combined retrieval used semantic/vector similarity and
PostgreSQL full-text search.

============================================================

INTERNAL SEC DATABASE CONTEXT

{context}

============================================================

INSTRUCTIONS

• Answer the user's question using the retrieved SEC context.

• Treat directly relevant retrieved information as the
  authoritative source.

• Use all relevant context necessary to answer the question.

• Keep companies, periods, forms, and documents distinct.

• If the question asks for analysis or a summary, synthesize
  the relevant information rather than discussing individual
  chunks separately.

• If the question asks for numerical information, verify the
  company, period, and units before answering.

• Do not invent missing information.

• If the retrieved context does not actually contain enough
  information to answer the question, say so.

• Always identify the source document used.

Citation rules:

- Cite only documents actually used to formulate the answer.
- Include the page when available.
- Do not mention chunk IDs.
- Do not mention vector scores.
- Do not mention internal retrieval metadata.
"""

    elif match_type == "vector":

        user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

VECTOR

Similarity Score

{best_score:.3f}

============================================================

INTERNAL SEC DATABASE CONTEXT

{context}

============================================================

INSTRUCTIONS

• Answer using ONLY the retrieved SEC context.

• The documents were retrieved primarily through semantic
  similarity.

• Do not assume semantic similarity means the document is
  exactly what the user requested.

• Verify the company, reporting period, form type, and document
  before using information.

• Do not invent missing information.

• If the retrieved context does not sufficiently support the
  answer, say:

"Not found in Internal Knowledge Base."

• Always identify the actual source document used.

Citation rules:

- Cite only documents actually used.
- Include page numbers when available.
- Do not mention chunk IDs.
- Do not mention similarity scores.
- Do not mention internal retrieval metadata.
"""

    elif match_type == "keyword":

        user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

KEYWORD

============================================================

INTERNAL SEC DATABASE CONTEXT

{context}

============================================================

INSTRUCTIONS

• Answer using ONLY the retrieved SEC context.

• The documents were retrieved primarily through keyword or
  PostgreSQL full-text matching.

• Make sure the retrieved text actually supports the answer.

• Do not infer missing information.

• Keep company names, reporting periods, forms, and documents
  distinct.

• If the retrieved context does not sufficiently support the
  answer, reply:

"Not found in Internal Knowledge Base."

• Always identify the actual source document used.

Citation rules:

- Cite only documents actually used.
- Include page numbers when available.
- Do not mention internal retrieval metadata.
"""
    elif match_type == "structured":

        user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

STRUCTURED

============================================================

INTERNAL SEC DATABASE CONTEXT

{context}

============================================================

INSTRUCTIONS

• Answer the user's question using the retrieved structured
  SEC/GIS information above.

• The structured information was extracted from the actual
  SEC/GIS document stored in the Internal Knowledge Base.

• Treat the retrieved structured information as authoritative.

• Use all relevant structured information necessary to answer
  the question.

• Preserve the relationships between fields, rows, values,
  and sections.

• If the user asks for a table, present the relevant information
  as a clear Markdown table.

• Keep companies, reporting periods, forms, and documents
  distinct.

• Do not invent missing information.

• Do not calculate or modify values unless the user explicitly
  asks for a calculation.

• If the retrieved structured context does not contain enough
  information to answer the question, reply exactly:

"Not found in Internal Knowledge Base."

• Always identify the actual source document used.

Citation rules:

- Cite only documents/sections actually used to formulate
  the answer.
- Include page numbers when available.
- Do not mention table IDs.
- Do not mention chunk IDs.
- Do not mention retrieval scores.
- Do not mention internal retrieval metadata.
"""
    elif match_type == "hybrid_structured":

      user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

HYBRID + STRUCTURED

============================================================

INTERNAL SEC DATABASE CONTEXT

{context}

============================================================

INSTRUCTIONS

• Answer the user's question using the retrieved SEC context.

• The context contains both:
  1. normal SEC document retrieval results, and
  2. structured SEC/GIS table data.

• When the question involves tables, financial figures,
  ownership, shares, percentages, balances, assets,
  liabilities, equity, revenue, expenses, or other
  structured information, prioritize the STRUCTURED SEC
  TABLE DATA when it directly supports the answer.

• Use normal document context for narrative explanations,
  definitions, qualifications, and surrounding filing context.

• Keep companies, reporting periods, forms, and documents
  distinct.

• Do not combine values from different companies or
  reporting periods unless the user explicitly asks for
  a comparison.

• Do not invent missing information.

• Preserve the exact values and units found in the source.

• If a calculation is requested, use only values present
  in the retrieved context.

• If the retrieved context does not sufficiently support
  the answer, reply:

"Not found in Internal Knowledge Base."

• Always identify the actual source document used.

Citation rules:

- Cite only documents/tables actually used.
- Include page numbers when available.
- Do not mention table IDs.
- Do not mention chunk IDs.
- Do not mention retrieval scores.
- Do not mention internal database implementation details.
"""
    else:

        user_prompt = f"""
USER QUESTION

{prompt}

============================================================

RETRIEVAL TYPE

NONE

============================================================

No relevant SEC documents were retrieved from the Internal
Knowledge Base.

If the answer is already established in the relevant chat
history, you may use that information.

Otherwise reply exactly:

Not found in Internal Knowledge Base.

Do not use outside knowledge.
"""

    messages.append({
        "role": "user",
        "content": user_prompt,
    })

    # ========================================================
    # GENERATE RESPONSE
    # ========================================================

    try:
        
        print("=" * 100)
        print("ACTUAL MESSAGES SENT TO SEC GPT")
        print("=" * 100)

        for i, message in enumerate(messages):
            content = message.get("content", "")

            print(f"\nMESSAGE {i}")
            print("ROLE:", message.get("role"))
            print("CONTENT LENGTH:", len(content))
            print("CONTENT PREVIEW:")
            print(content[:500])

        print("=" * 100)
        
        response = client.chat.completions.create(
            model="gpt-5.6-luna",
            messages=messages,
            max_completion_tokens=1500,
        )

        return (
            response
            .choices[0]
            .message
            .content
        )

    except Exception as e:

        print(
            f"SEC GPT ERROR: {str(e)}"
        )

        return (
            f"Error generating AI response: {e}"
        )