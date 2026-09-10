# rag/sec_conversation.py

import json
from openai import OpenAI
from gptchatbot.settings import OPENAI_API_KEY

openai_client = OpenAI(api_key=OPENAI_API_KEY)

CONVERSATION_CONTEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_follow_up": {
            "type": "boolean"
        },

        "referenced_company": {
            "type": "string"
        },

        "referenced_sec_no": {
            "type": "string"
        },

        "referenced_topic_ids": {
            "type": "array",
            "items": {
                "type": "integer"
            }
        },

        "resolved_question": {
            "type": "string"
        },

        "structured_query": {
            "type": "object",
            "properties": {
                "is_structured_request": {
                    "type": "boolean"
                },

                "subject": {
                    "type": "string"
                },

                "search_terms": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            },
            "required": [
                "is_structured_request",
                "subject",
                "search_terms"
            ],
            "additionalProperties": False
        }
    },

    "required": [
        "is_follow_up",
        "referenced_company",
        "referenced_sec_no",
        "referenced_topic_ids",
        "resolved_question",
        "structured_query"
    ],

    "additionalProperties": False
}

def resolve_sec_conversation(
    prompt,
    history,
):
    """
    Resolve references such as:

        "its tables"
        "that company"
        "the same company"
        "give me its financials"

    using the previous conversation context.
    """

    if not history:
        return {
            "is_follow_up": False,
            "referenced_company": "",
            "referenced_sec_no": "",
            "referenced_topic_ids": [],
            "resolved_question": prompt,
        }

    ###########################################################
    # GET MOST RECENT STRUCTURED CONTEXT
    ###########################################################

    previous_context = {}

    for message in reversed(history):

        if not isinstance(message, dict):
            continue

        context = message.get(
            "conversation_context"
        )

        if isinstance(context, dict):

            previous_context = context

            break


    ###########################################################
    # PREVIOUS CONVERSATION
    ###########################################################

    conversation = json.dumps(
        history,
        ensure_ascii=False,
        default=str,
    )

    structured_context = json.dumps(
        previous_context,
        ensure_ascii=False,
        default=str,
    )


    ###########################################################
    # RESOLVER PROMPT
    ###########################################################

    resolver_prompt = f"""
You are resolving context for a document knowledge-base search.

CURRENT USER QUESTION:
{prompt}

MOST RECENT STRUCTURED CONVERSATION CONTEXT:
{structured_context}

PREVIOUS CONVERSATION:
{conversation}

Determine whether the current question refers to something
from the previous conversation.

Examples:

Previous:
User: Tell me about ABC Corporation.
Assistant: ABC Corporation reported...

Current:
"Give me its tables."

Resolve:
referenced_company = "ABC Corporation"

Previous:
User: Tell me about ABC Corporation.
Current:
"What is its revenue?"

Resolve:
referenced_company = "ABC Corporation"

Previous:
User: Tell me about ABC Corporation.
Current:
"Now tell me about XYZ Corporation."

This is NOT a follow-up to ABC Corporation.

Rules:

1. Resolve pronouns such as:
   - it
   - its
   - they
   - their
   - that company
   - the company
   - this company

2. Prefer the MOST RECENT structured conversation
   context when resolving references.

3. If structured topic IDs are available and the
   current question refers to the previous company,
   preserve those topic IDs.

4. Do not invent a company.

5. If there is no previous context, leave the
   referenced fields empty.

6. An explicit company in the CURRENT question always
   overrides the previous conversation context.

7. Rewrite the current question into a standalone question
   using the resolved company when possible.
   
8. A question can be a follow-up even when it does not contain a
   pronoun. If the user asks for additional information, tables,
   directors, officers, shareholders, capital, addresses, or other
   details immediately after discussing a specific company/document,
   inherit the most recent relevant company/document context unless
   the user explicitly names a different company.

9. For requests such as:
   - "give me a table for the directors and officers"
   - "show me the shareholders"
   - "what are its officers"
   - "give me the capital structure"
   - "show the addresses"

   preserve the most recent company, SEC number, form type,
   period, and topic IDs from the structured conversation context.

Return only the required JSON.

STRUCTURED REQUEST DETECTION

Determine whether the current question asks for a specific
structured section, table, list, or category of information
from the referenced document.

Examples:

"Give me a table for the directors and officers"
→ is_structured_request = true
→ subject = "directors and officers"

"Show me its stockholder information"
→ is_structured_request = true
→ subject = "stockholder information"

"What is the authorized capital?"
→ is_structured_request = true
→ subject = "authorized capital"

"Give me the registered address"
→ is_structured_request = true
→ subject = "registered address"

"Summarize the entire GIS"
→ is_structured_request = false

"Tell me about the company"
→ is_structured_request = false

For structured requests, generate a small number of useful
search terms that may appear in the structured section title,
raw content, or extracted data.

Do not generate overly broad terms.

For example:

"stockholder information"
→ ["stockholder", "shareholder", "ownership", "shares"]

"directors and officers"
→ ["director", "officer", "board"]

"authorized capital"
→ ["authorized capital", "capital"]

The search terms are used only to retrieve relevant records
from the Internal SEC Database. They are not the answer.

A question may be a follow-up even when it does not contain
a pronoun.

If the previous conversation established a specific company,
document, form, or reporting period, and the current question
asks for additional information about it, inherit that context
unless the user explicitly names a different company.

Examples:

"Give me a table for the directors and officers"
"Show me the stockholders"
"What is the capital?"
"Give me the address"

These should inherit the most recent relevant company,
document, form, period, and topic IDs.
"""

    ###########################################################
    # GPT RESOLUTION
    ###########################################################

    response = openai_client.responses.create(
        model="gpt-5.6-luna",

        input=resolver_prompt,

        text={
            "format": {
                "type": "json_schema",
                "name": "conversation_context",
                "strict": True,
                "schema": CONVERSATION_CONTEXT_SCHEMA,
            }
        },
    )

    return json.loads(
        response.output_text
    )