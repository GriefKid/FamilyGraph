"""Central validation and normalisation for untrusted user uploads."""

from __future__ import annotations

import io
import uuid
import warnings

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


MEBIBYTE = 1024 * 1024
DEFAULT_IMAGE_BYTES = 10 * MEBIBYTE
DEFAULT_IMAGE_PIXELS = 40_000_000
DEFAULT_IMAGE_DIMENSION = 4096

_IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


class UploadValidationError(ValueError):
    """A safe, user-facing validation error for an uploaded file."""


def read_limited_upload(uploaded, *, max_bytes: int, label: str = "فایل") -> bytes:
    """Read at most ``max_bytes`` and reject lying or missing size metadata."""
    if uploaded is None:
        raise UploadValidationError(f"{label} ارسال نشده است.")
    declared_size = getattr(uploaded, "size", None)
    if declared_size is not None and declared_size > max_bytes:
        raise UploadValidationError(
            f"حجم {label} نباید بیشتر از {max_bytes // MEBIBYTE} مگابایت باشد."
        )
    try:
        uploaded.seek(0)
        data = uploaded.read(max_bytes + 1)
    except (AttributeError, OSError) as exc:
        raise UploadValidationError(f"{label} قابل خواندن نیست.") from exc
    finally:
        try:
            uploaded.seek(0)
        except (AttributeError, OSError):
            pass
    if not data:
        raise UploadValidationError(f"{label} خالی است.")
    if len(data) > max_bytes:
        raise UploadValidationError(
            f"حجم {label} نباید بیشتر از {max_bytes // MEBIBYTE} مگابایت باشد."
        )
    return data


def normalize_image_upload(
    uploaded,
    *,
    max_bytes: int = DEFAULT_IMAGE_BYTES,
    max_pixels: int = DEFAULT_IMAGE_PIXELS,
    max_dimension: int = DEFAULT_IMAGE_DIMENSION,
    label: str = "تصویر",
):
    """Validate, strip metadata, resize and give an image a random safe name."""
    data = read_limited_upload(uploaded, max_bytes=max_bytes, label=label)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = (probe.format or "").upper()
                width, height = probe.size
                probe.verify()
            if image_format not in _IMAGE_FORMATS:
                raise UploadValidationError("فقط تصویر JPEG، PNG یا WebP مجاز است.")
            if width < 1 or height < 1 or width * height > max_pixels:
                raise UploadValidationError("ابعاد تصویر بیش از حد بزرگ یا نامعتبر است.")
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                image = ImageOps.exif_transpose(source).copy()
    except UploadValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise UploadValidationError("ابعاد تصویر بیش از حد بزرگ است.")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise UploadValidationError("فایل انتخاب‌شده یک تصویر سالم نیست.") from exc

    if image.width > max_dimension or image.height > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    extension, content_type = _IMAGE_FORMATS[image_format]
    output = io.BytesIO()
    if image_format == "JPEG":
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(output, "JPEG", quality=88, optimize=True, progressive=True)
    elif image_format == "PNG":
        image.save(output, "PNG", optimize=True)
    else:
        image.save(output, "WEBP", quality=88, method=6)

    payload = output.getvalue()
    if len(payload) > max_bytes:
        raise UploadValidationError(
            f"حجم {label} پس از پردازش هنوز بیشتر از {max_bytes // MEBIBYTE} مگابایت است."
        )
    clean = ContentFile(payload, name=f"{uuid.uuid4().hex}.{extension}")
    clean.content_type = content_type
    return clean


def validate_image_file(value) -> None:
    """Model/form validator; request handlers additionally keep the normalised file."""
    try:
        normalize_image_upload(value)
    except UploadValidationError as exc:
        raise ValidationError(str(exc), code="invalid_image_upload") from exc
    finally:
        try:
            value.seek(0)
        except (AttributeError, OSError):
            pass


def normalize_audio_upload(uploaded, *, max_bytes: int = 15 * MEBIBYTE):
    """Validate common browser audio containers by signature and randomise the name."""
    data = read_limited_upload(uploaded, max_bytes=max_bytes, label="فایل صوتی")
    head = data[:64]
    if head.startswith(b"\x1aE\xdf\xa3"):
        extension, content_type = "webm", "audio/webm"
    elif head.startswith(b"OggS"):
        extension, content_type = "ogg", "audio/ogg"
    elif head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        extension, content_type = "wav", "audio/wav"
    elif head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        extension, content_type = "mp3", "audio/mpeg"
    elif len(head) >= 12 and head[4:8] == b"ftyp":
        extension, content_type = "m4a", "audio/mp4"
    else:
        raise UploadValidationError("فرمت واقعی فایل صوتی پشتیبانی نمی‌شود.")
    clean = ContentFile(data, name=f"{uuid.uuid4().hex}.{extension}")
    clean.content_type = content_type
    return clean
