class AttachmentReadError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class AttachmentResolveError(AttachmentReadError):
    def __init__(self, attachment_ref: str, reason: str):
        self.attachment_ref = attachment_ref
        self.reason = reason
        super().__init__(f"Cannot resolve attachment '{attachment_ref}': {reason}")


class AttachmentTextReadError(AttachmentReadError):
    def __init__(self, attachment_ref: str, reason: str):
        self.attachment_ref = attachment_ref
        self.reason = reason
        super().__init__(
            f"Text read failed for attachment '{attachment_ref}': {reason}"
        )


class AttachmentOcrError(AttachmentReadError):
    def __init__(self, attachment_ref: str, reason: str):
        self.attachment_ref = attachment_ref
        self.reason = reason
        super().__init__(f"OCR failed for attachment '{attachment_ref}': {reason}")


class AttachmentUnsupportedTypeError(AttachmentReadError):
    def __init__(self, attachment_ref: str, reason: str):
        self.attachment_ref = attachment_ref
        self.reason = reason
        super().__init__(f"Unsupported attachment '{attachment_ref}': {reason}")
