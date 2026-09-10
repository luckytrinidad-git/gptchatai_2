import json
import os

from django.db import transaction, connections
from ninja import File
from ninja.files import UploadedFile
from ninja import Router, Form, File
from rag.embeddings import get_embedding
from rag.schemas import PromptInput
from general.utilities.ipfs_utilities import upload_to_ipfs

from revie.tasks import generate_revie_embeddings, import_revie_data
from revie.utils import search_revie_knowledge_base
from revie.models import (
    RevieIntent,
    RevieQuestion,
)

from chatbot_models.revie_model import openai_gpt45
from chatbot_models.openai_model import openai_gpt45 as bir_openai_gpt45

router = Router(tags=["Internal BIR AI"])

@router.post("/intents/import")
def import_revie_intents(
    request,
    file: UploadedFile = File(...),
    title: str = Form(...),
    agent: str = Form(...),
    uploaded_by: str = Form("Admin"),
):
    print("=" * 70)
    print("REVIE IMPORT REQUEST")
    print(f"File: {file.name}")
    print("=" * 70)

    ###########################################################
    # 1. READ FILE
    ###########################################################

    try:

        print("Reading JSON file...")

        file_bytes = file.read()

        print(
            f"File size: "
            f"{len(file_bytes) / (1024 * 1024):.2f} MB"
        )

        data = json.loads(file_bytes)

        if not isinstance(data, list):

            return {
                "success": False,
                "error": (
                    "Invalid format. "
                    "Expected a JSON array."
                ),
            }

        print(f"JSON loaded successfully: {len(data)} items")

    except json.JSONDecodeError as e:

        return {
            "success": False,
            "error": f"Invalid JSON file: {str(e)}",
        }

    except Exception as e:

        return {
            "success": False,
            "error": f"Unable to read JSON file: {str(e)}",
        }

    ###########################################################
    # 2. VALIDATE ITEMS
    ###########################################################

    print("Validating JSON items...")

    validation_errors = []

    for index, item in enumerate(data):

        if not isinstance(item, dict):

            validation_errors.append(
                f"Item {index} is not an object."
            )

            continue

        if "id" not in item:

            validation_errors.append(
                f"Item {index} is missing 'id'."
            )

        if "answer" not in item:

            validation_errors.append(
                f"Item {index} is missing 'answer'."
            )

        if "questions" not in item:

            validation_errors.append(
                f"Item {index} is missing 'questions'."
            )

        elif not isinstance(
            item["questions"],
            list,
        ):

            validation_errors.append(
                f"Item {index} 'questions' must be a list."
            )

        # Avoid returning thousands of errors
        if len(validation_errors) >= 100:

            validation_errors.append(
                "Additional validation errors were omitted."
            )

            break

    if validation_errors:

        print(
            f"Validation failed: "
            f"{len(validation_errors)} errors"
        )

        return {
            "success": False,
            "error": "Validation failed.",
            "validation_errors": validation_errors,
        }

    print("Validation successful.")

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
                (agent,),
            )

            row = cursor.fetchone()

        if not row:

            return {
                "success": False,
                "error": f"Agent with ID {agent} not found.",
            }

        agent_name = row[0]

    except Exception as e:

        return {
            "success": False,
            "error": f"Unable to retrieve agent: {str(e)}",
        }

    ###########################################################
    # 4. UPLOAD TO IPFS
    ###########################################################

    try:

        print("Uploading JSON to IPFS...")

        file_cid = upload_to_ipfs(
            file_name=file.name,
            file_bytes=file_bytes,
            content_type=(
                file.content_type
                or "application/json"
            ),
        )

        print(f"IPFS upload successful: {file_cid}")

    except Exception as e:

        print(f"IPFS upload failed: {e}")

        return {
            "success": False,
            "error": f"IPFS upload failed: {str(e)}",
        }

    ###########################################################
    # 5. CREATE MASTER RECORD
    ###########################################################

    try:

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

    except Exception as e:

        print(
            f"Failed creating master record: {e}"
        )

        return {
            "success": False,
            "error": (
                "Failed to create knowledge "
                f"record: {str(e)}"
            ),
        }

    ###########################################################
    # 6. QUEUE CELERY IMPORT
    ###########################################################

    try:

        print(
            f"Queueing REVIE import "
            f"for topic {topic_id}..."
        )

        task = import_revie_data.delay(
            topic_id=topic_id,
            data=data,
        )

        print(
            f"REVIE import queued. "
            f"Task ID: {task.id}"
        )

    except Exception as e:

        print(
            f"Unable to queue Celery task: {e}"
        )

        return {
            "success": True,
            "message": (
                "Knowledge record created successfully, "
                "but the REVIE import could not be queued."
            ),
            "topic_id": topic_id,
            "file_cid": file_cid,
            "task_error": str(e),
        }

    ###########################################################
    # 7. RETURN IMMEDIATELY
    ###########################################################

    return {
        "success": True,
        "message": (
            "REVIE import queued successfully. "
            "Processing will continue in the background."
        ),
        "topic_id": topic_id,
        "file_cid": file_cid,
        "items": len(data),
        "task_id": task.id,
        "status": "queued",
    }

@router.post("/intents/embed")
def start_revie_embedding(request):

    task = generate_revie_embeddings.delay()

    return {
        "success": True,
        "message": "Embedding generation started.",
        "task_id": task.id,
    }

@router.get("/intents/status")
def revie_intent_status(request):

    total = (
        RevieQuestion.objects
        .using("birai_db")
        .count()
    )

    embedded = (
        RevieQuestion.objects
        .using("birai_db")
        .filter(
            embedding__isnull=False
        )
        .count()
    )

    pending = total - embedded

    return {
        "success": True,
        "intents": (
            RevieIntent.objects
            .using("birai_db")
            .count()
        ),
        "questions": total,
        "embedded": embedded,
        "pending": pending,
        "complete": (
            total > 0 and pending == 0
        ),
    }

@router.post("/ask-revie")
def ask_revie(request, data: PromptInput):
    prompt = data.prompt
    
    ###########################################################
    # 1. CHAT HISTORY
    ###########################################################

    agent_name = data.agent

    try:
        history = json.loads(data.history)
    except Exception:
        history = []

    ###########################################################
    # 2. EMBEDDING
    ###########################################################

    query_embedding = get_embedding(prompt)

    ###########################################################
    # 3. RETRIEVAL
    ###########################################################

    retrieval = search_revie_knowledge_base(
        query_embedding=query_embedding,
        limit=5,
        score_threshold=0.50,
    )

    ###########################################################
    # 4. CONTEXT
    ###########################################################

    if retrieval["contexts"]:

        context = "\n\n".join(
            retrieval["contexts"]
        )

        no_docs_found = False

    else:

        no_docs_found = True

        context = (
            "No relevant documents were found in the "
            "Internal Knowledge Base."
        )


    ###########################################################
    # 5. LOG RETRIEVAL
    ###########################################################

    print("=" * 70)
    print(f"Agent      : {agent_name}")
    print(f"Match Type : {retrieval['match_type']}")
    print(f"Best Score : {retrieval['best_score']}")
    print(f"Documents  : {len(retrieval['contexts'])}")
    print("=" * 70)


    ###########################################################
    # 6. GPT
    ###########################################################

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
            match_type=retrieval["match_type"],
            best_score=retrieval["best_score"],
        )


    ###########################################################
    # 7. RETURN
    ###########################################################

    return {
        "response": response,
        "match_type": retrieval["match_type"],
        "score": retrieval["best_score"],
        "documents": len(
            retrieval["contexts"]
        ),
    }