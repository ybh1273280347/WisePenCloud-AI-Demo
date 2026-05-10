#!/usr/bin/env bash
set -euo pipefail

echo "=== Web Fetch / Document Parse Decoupling Audit ==="
echo ""

echo "== web_fetch forbidden symbols =="
grep -r "force_browser\|force_ocr\|DocumentParser\|get_docling_parser\|LocalOcrProcessor\|OcrResult\|OcrSupplement\|Docling\|docling\|PaddleOCR\|PPStructure\|camelot\|pymupdf\|fitz\|pdfplumber\|pdf_render" src/chat/application/web_fetch 2>/dev/null || true

echo ""
echo "== document_parse forbidden pdf deps =="
grep -r "docling\|Docling\|markitdown\|MarkItDown\|DocumentParser\|pdfplumber" src/chat/application/document_parse/pdf 2>/dev/null || true

echo ""
echo "== document_parse unsupported formats =="
grep -r "html\|htm\|txt\|md\|markdown\|csv\|json\|xml\|png\|jpg\|jpeg\|tiff\|bmp\|webp\|gif\|mp3\|mp4\|wav\|mov" src/chat/application/document_parse 2>/dev/null || true

echo ""
echo "== stale web_fetch OCR config names =="
grep -r "WEB_FETCH.*OCR\|WEB_FETCH.*PDF\|WEB_FETCH.*DOCUMENT" src/chat tests docs 2>/dev/null || true

echo ""
echo "== __all__ =="
grep -r "__all__" src/chat 2>/dev/null || true

echo ""
echo "== private imports =="
grep -r "from .* import _\|import .*._" src/chat 2>/dev/null || true

echo ""
echo "=== Audit complete ==="
