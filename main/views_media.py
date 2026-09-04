"""Permission-checked delivery for user-uploaded media."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404

from .models import Friendship, JournalImage, Node, SocialPost, User


def _normalise_path(raw_path: str) -> str:
    if not raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise Http404
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise Http404
    return str(path)


def _can_view_profile_image(viewer, profile: User) -> bool:
    if not viewer.is_authenticated:
        return False
    if viewer.pk == profile.pk or profile.is_public:
        return True
    return Friendship.objects.filter(user=viewer, friend=profile).exists()


def _authorised_media(request, path: str) -> tuple[bool, bool]:
    """Return ``(allowed, public_cache)`` without exposing unreferenced files."""
    journal = JournalImage.objects.filter(image=path).select_related("entry").first()
    if journal:
        owner_id = journal.owner_id or getattr(journal.entry, "owner_id", None)
        return bool(request.user.is_authenticated and request.user.pk == owner_id), False

    profile = User.objects.filter(Q(avatar=path) | Q(cover_image=path)).first()
    if profile:
        return _can_view_profile_image(request.user, profile), bool(profile.is_public)

    node = Node.objects.filter(picture=path).select_related("owner").first()
    if node:
        own = request.user.is_authenticated and request.user.pk == node.owner_id
        public = bool(node.is_public and node.owner and node.owner.is_public)
        return bool(own or (request.user.is_authenticated and public)), public

    post = SocialPost.objects.filter(image=path).select_related("author").first()
    if post:
        own = request.user.is_authenticated and request.user.pk == post.author_id
        public = bool(post.is_public and post.author.is_public)
        return bool(own or (request.user.is_authenticated and public)), public

    return False, False


def protected_media(request, path: str):
    path = _normalise_path(path)
    allowed, public_cache = _authorised_media(request, path)
    if not allowed or not default_storage.exists(path):
        raise Http404
    try:
        handle = default_storage.open(path, "rb")
    except OSError as exc:
        raise Http404 from exc
    content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600" if public_cache else "private, no-store"
    return response
