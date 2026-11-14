"""Luminary API package.

Registers pillow-heif's HEIC/HEIF opener with Pillow once, here, so every
`Image.open()` anywhere in the app (sync, face tagging/recognition, chat)
can decode the .heic/.heif files `sync.IMAGE_EXTENSIONS` already indexes --
Pillow ships no HEIF decoder of its own. Registration is process-global, so
it belongs at package import rather than in each module that opens images.
"""

from pillow_heif import register_heif_opener

register_heif_opener()
