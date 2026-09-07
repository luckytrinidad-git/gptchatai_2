from ninja import NinjaAPI
from ninja.security import APIKeyHeader
from django.conf import settings

from gemini_ai.api import router as gemini_router
from open_ai.api import router as openai_router
from rag.api import router as rag_router
from general.api import router as general_router
from revie.api import router as revie_router
from sec.api import router as sec_router

class ApiKey(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        if key == settings.X_API_KEY:
            return key

        return None


api = NinjaAPI(
    auth=ApiKey(),
    title="GPTChatbot",
    description="GPTChatbot",
    openapi_url="/gptchatbot.json"
)

api.add_router("/gemini/", gemini_router)
api.add_router("/openai/", openai_router)
api.add_router("/rag/", rag_router)
api.add_router("/revie/", revie_router)
api.add_router("/sec/", sec_router)
api.add_router("/general/", general_router)