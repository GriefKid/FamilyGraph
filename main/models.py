from django.core.exceptions import ValidationError
from django.db import models

class Node(models.Model):
    username = models.CharField(max_length=100, unique=True)
    picture = models.ImageField(upload_to="media/", blank=True, null=True)
    name = models.CharField(max_length=200, blank=True)
    birth_day = models.DateField(blank=True, null=True)
    career = models.CharField(max_length=200, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.username or f"Node {self.pk}"
    class Meta:
        ordering = ['username']

class Relationship(models.Model):
    rel = models.CharField(max_length=100, blank=True, null=True)
    source = models.ForeignKey(Node, related_name='as_source', on_delete=models.PROTECT)
    target = models.ForeignKey(Node, related_name='as_target', on_delete=models.PROTECT)

    def __str__(self):
        return self.rel

    def clean(self):
        if self.source == self.target:
            raise ValidationError("A node cannot have a relationship with itself.")

    class Meta:
        unique_together = ('source', 'target', 'rel')

class Information(models.Model):

    VISIBILITY_CHOICES = [
        ('public', 'Everyone can see'),
        ('shared', 'Someone can see'),
        ('private', 'Nobody'),
    ]

    node = models.ForeignKey(
        Node,
        on_delete=models.PROTECT,
        related_name='informations'
    )

    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default='private'
    )

    data = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f'Information #{self.id} - {self.node}'

