"""Camera-sized photos are accepted and stored at web size."""
import io

import pytest
from fastapi import HTTPException
from PIL import Image

from vendoo_studio.services import hosted_photos


def jpeg(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (width, height), 'navy').save(output, 'JPEG', quality=60)
    return output.getvalue()


@pytest.mark.parametrize('size', [(4500, 6000), (6000, 6000), (6000, 8000)])
def test_camera_photos_are_accepted_and_shrunk(size):
    # A seller's Mac held 27 and 36 megapixel photos; refusing them stopped a move.
    data, width, height = hosted_photos.normalize(jpeg(*size))
    assert max(width, height) == 2400
    with Image.open(io.BytesIO(data)) as stored:
        assert stored.format == 'JPEG' and stored.size == (width, height)


def test_oversized_images_are_still_refused():
    with pytest.raises(HTTPException) as refused:
        hosted_photos.normalize(jpeg(8000, 7000))
    assert refused.value.status_code == 422
    assert '50 megapixels' in refused.value.detail
