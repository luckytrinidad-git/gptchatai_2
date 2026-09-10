from openai import OpenAI


client = OpenAI(
    max_retries=0,
    timeout=120.0,
)

def get_embedding(texts: list[str]):
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    embeddings = sorted(
        response.data,
        key=lambda item: item.index,
    )
    return [
        item.embedding
        for item in embeddings
    ]