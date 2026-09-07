from django.db import connections
from ninja import Router
from ninja.files import UploadedFile
from sec.schemas import LogInput
from datetime import datetime

router = Router(tags=["Internal BIR AI"])

@router.get("/agents")
def get_agents(request):
    with connections["birai_db"].cursor() as cursor:
        cursor.execute("""
            SELECT id, agent
            FROM kx_agents
            ORDER BY agent;
        """)

        return [
            {
                "id": row[0],
                "agent": row[1]
            }
            for row in cursor.fetchall()
        ]

@router.get("/topics")
def get_topics(request):
    
    with connections["birai_db"].cursor() as cursor:
        cursor.execute("""
            SELECT
                t.id,
                t.topic_title,
                t.agent,
                t.file_name,
                t.uploaded_at,
                t.file_cid
            FROM kx_topics t
            LEFT JOIN rag_birdocument r
                ON t.id = r.topic_id
            GROUP BY
                t.id,
                t.topic_title,
                t.agent,
                t.file_name,
                t.uploaded_at,
                t.file_cid
            ORDER BY t.uploaded_at DESC
        """)

        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    return [
        dict(zip(columns, row))
        for row in rows
    ]
    

@router.get("/audit_log")
def get_audit_log(request):
    
    with connections["birai_db"].cursor() as cursor:
        cursor.execute("""
            SELECT timestamp as Timestamp, username as Username, action as Action, module as Module, status as Status 
            FROM audit_logs 
            ORDER BY timestamp DESC
        """)

        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    return [
        dict(zip(columns, row))
        for row in rows
    ]
    
@router.post("/log_audit")
def log_augit(request, data: LogInput):
    try:
        # Use the Django connection managed in your settings
        conn = connections["birai_db"]
        
        with conn.cursor() as cursor:
            query = """
                INSERT INTO audit_logs (timestamp, username, action, module, status)
                VALUES (%s, %s, %s, %s, %s)
            """
            # Use %s placeholders to prevent SQL Injection
            cursor.execute(query, [
                datetime.now(), 
                data.username, 
                data.action, 
                data.module, 
                data.status
            ])
            # Django's 'connections' usually auto-commits, but just in case:
            if not conn.get_autocommit():
                conn.commit()
                
    except Exception as e:
        # We don't want a logging error to crash the whole app
        print(f"FAILED TO WRITE TO AUDIT LOG: {e}")