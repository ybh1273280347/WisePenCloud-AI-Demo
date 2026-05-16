from enum import Enum


class InternalReferenceKind(str, Enum):
    CONTENT_ID = "content_id"
    FILE_REF = "file_ref"
    DOWNLOAD_REF = "download_ref"
    ATTACHMENT_REF = "attachment_ref"
    IMAGE_REF = "image_ref"
