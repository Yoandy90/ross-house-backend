"""Canonical product photos: 1200 square, white contain, metadata-free WebP."""
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 3_000_000  # Keeps base64 requests below the web hosting body limit.
MAX_OUTPUT_BYTES = 250_000
MAX_PIXELS = 25_000_000
SIZE = 1200


def normalize_product_photo(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ValueError('La foto debe pesar como máximo 3 MB.')
    try:
        with Image.open(BytesIO(raw)) as original:
            if original.format not in {'JPEG', 'PNG', 'WEBP'} or getattr(original, 'n_frames', 1) != 1:
                raise ValueError('Usa una foto estática JPG, PNG o WebP.')
            if original.width * original.height > MAX_PIXELS:
                raise ValueError('La foto debe tener como máximo 25 megapíxeles.')
            photo = ImageOps.exif_transpose(original).convert('RGBA')
            photo.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
            canvas = Image.new('RGB', (SIZE, SIZE), 'white')
            canvas.paste(photo, ((SIZE-photo.width)//2, (SIZE-photo.height)//2), photo)
            for quality in (85, 78, 70, 60, 50, 40):
                output = BytesIO()
                canvas.save(output, format='WEBP', quality=quality, method=6)
                if output.tell() <= MAX_OUTPUT_BYTES:
                    return output.getvalue()
            raise ValueError('La imagen tiene demasiado detalle. Elige otra foto.')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('No se pudo leer la foto. Usa JPG, PNG o WebP.') from exc
