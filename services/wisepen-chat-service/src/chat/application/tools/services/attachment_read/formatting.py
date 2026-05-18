from typing import List

from .models import AttachmentReadResult, AttachmentStatus


def format_attachment_read_result(result: AttachmentReadResult) -> str:
    lines: List[str] = ["[Tool Result] attachment_read", "", "Attachments:"]

    file_refs: List[str] = []
    for index, item in enumerate(result.items, start=1):
        lines.append(f"{index}. file_name: {item.file_name}")
        lines.append(f"   attachment_ref: {item.attachment_ref}")
        lines.append(f"   mime_type: {item.mime_type}")
        lines.append(f"   kind: {item.kind}")
        lines.append(f"   size_bytes: {item.size_bytes}")
        lines.append(f"   status: {item.status}")

        if item.content_block is not None:
            lines.append("   content:")
            lines.append(item.content_block)

        if item.preview is not None:
            lines.append("   preview:")
            lines.append(item.preview)

        if item.ocr_content_block is not None:
            lines.append("   ocr_content:")
            lines.append(item.ocr_content_block)

        if item.ocr_preview is not None:
            lines.append("   ocr_preview:")
            lines.append(item.ocr_preview)

        if item.image_ref is not None:
            lines.append(f"   image_ref: {item.image_ref}")
            lines.append(
                f"   image_available_for_vision: {str(item.image_available_for_vision).lower()}"
            )

        if item.file_ref is not None:
            lines.append(f"   file_ref: {item.file_ref}")
            lines.append("   next_step: Pass this file_ref to document_parse.")

        if item.error is not None:
            lines.append(f"   error: {item.error}")

        if item.status == AttachmentStatus.OCR_COMPLETED.value:
            lines.append(
                "   note: OCR text is only text extracted from the image. "
                "It does not replace visual analysis of the image."
            )
        elif item.status == AttachmentStatus.OCR_FAILED.value:
            lines.append(
                "   note: OCR failure does not mean the image is unreadable. "
                "The model should inspect the image_ref if visual analysis is needed."
            )

        lines.append("")

        if (
            item.status == AttachmentStatus.DOCUMENT_PARSE_REQUIRED.value
            and item.file_ref
        ):
            file_refs.append(item.file_ref)

    if file_refs:
        lines.append("Document parse required:")
        for file_ref in file_refs:
            lines.append(f"- {file_ref}")
        lines.append("")

    lines.extend(
        [
            "Assistant instructions:",
            "- Use tool_content_read to continue reading when content_id is returned.",
            "- For images, OCR is always attempted before returning image_ref.",
            "- OCR text is only extracted text from the image. It does not replace visual analysis.",
            "- If image_ref is returned, inspect the image with the model's own vision capability when visual details, layout, objects, charts, screenshots, or handwriting matter.",
            "- If OCR failed, do not treat the image as unreadable. Use image_ref for visual analysis.",
            "- Use tool_content_read to continue reading OCR text when ocr_content returns a content_id.",
            '- Call document_parse once with all file_refs listed under "Document parse required".',
            "- These file_refs are temporary handoff references and may expire after TTL.",
            "- Do not call document_parse one file at a time.",
            "- Do not pass attachment_ref or content_id to document_parse.",
            "- Do not pass file_ref to attachment_read.",
            "- Do not infer document content before document_parse returns.",
            "- Do not infer deferred or unsupported attachment content.",
        ]
    )

    return "\n".join(lines).rstrip()
