from django.db import models
from pgvector.django import VectorField

class KXTopics(models.Model):
    topic_title = models.CharField(max_length=255)
    file_name = models.CharField(max_length=255)

    uploaded_at = models.DateTimeField()
    uploaded_by = models.CharField(max_length=255)

    agent = models.CharField(max_length=255)
    agent_id = models.IntegerField()

    file_cid = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    company_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    sec_no = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    form_type = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    period_covered = models.CharField(
        max_length=100,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "kx_topics"
        
class SecDocument(models.Model):
    kx_topic = models.ForeignKey(
        "KXTopics",
        on_delete=models.CASCADE,
        db_column="kx_topic_id",
        related_name="documents",
    )

    content = models.TextField()

    page_number = models.IntegerField(null=True, blank=True)

    section_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )

    chunk_index = models.IntegerField(
        null=True,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    embedding = VectorField(
        dimensions=1536,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        db_table = "sec_document"
        ordering = ["kx_topic_id", "page_number", "chunk_index"]