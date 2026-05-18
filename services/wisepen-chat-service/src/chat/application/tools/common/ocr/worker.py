import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from chat.application.tools.common.errors.document_parse import DocumentParserDependencyError

_PROTOCOL_STDOUT = sys.stdout
_ENGINES: Dict[Tuple[str, bool, bool, bool], object] = {}


@contextlib.contextmanager
def _redirect_stdout_to_stderr():
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = original_stdout


def _write_response(response: Dict[str, Any]) -> None:
    _PROTOCOL_STDOUT.write(json.dumps(response, ensure_ascii=False) + "\n")
    _PROTOCOL_STDOUT.flush()


def _get_engine(
    *,
    language: str,
    use_doc_orientation_classify: bool,
    use_doc_unwarping: bool,
    use_textline_orientation: bool,
):
    key = (
        language,
        use_doc_orientation_classify,
        use_doc_unwarping,
        use_textline_orientation,
    )

    if key in _ENGINES:
        return _ENGINES[key]

    if _ENGINES and key not in _ENGINES:
        _ENGINES.clear()

    try:
        with _redirect_stdout_to_stderr():
            import torch

            assert torch is sys.modules.get("torch"), (
                "import torch 必须在 from paddleocr import PaddleOCR 之前执行，"
                "否则 Windows 上 paddleocr -> albumentations -> torch 的间接导入链"
                "会导致 shm.dll 加载失败 ([WinError 127])。请勿删除此 import。"
            )
            from paddleocr import PaddleOCR
    except ImportError as e:
        raise DocumentParserDependencyError("paddleocr", str(e)) from e

    kwargs = {
        "lang": language,
        "use_angle_cls": False,
        "show_log": False,
    }

    with _redirect_stdout_to_stderr():
        engine = PaddleOCR(**kwargs)

    _ENGINES[key] = engine
    return engine


def _collect_legacy_ocr_texts(raw_result) -> List[str]:
    texts: List[str] = []

    def visit(value) -> None:
        if value is None:
            return

        if isinstance(value, tuple) and value:
            first = value[0]
            if isinstance(first, str):
                text = first.strip()
                if text:
                    texts.append(text)
                return

        if isinstance(value, list):
            if len(value) >= 2 and isinstance(value[1], tuple):
                text_value = value[1][0] if len(value[1]) >= 1 else None
                if isinstance(text_value, str):
                    text = text_value.strip()
                    if text:
                        texts.append(text)
                    return

            for item in value:
                visit(item)

    visit(raw_result)
    return texts


def _recognize_image(
    *,
    input_path: Path,
    language: str,
    use_doc_orientation_classify: bool,
    use_doc_unwarping: bool,
    use_textline_orientation: bool,
) -> Dict[str, Any]:
    engine = _get_engine(
        language=language,
        use_doc_orientation_classify=use_doc_orientation_classify,
        use_doc_unwarping=use_doc_unwarping,
        use_textline_orientation=use_textline_orientation,
    )

    with _redirect_stdout_to_stderr():
        raw_result = engine.ocr(str(input_path), cls=False)

    lines = _collect_legacy_ocr_texts(raw_result)
    text = "\n".join(lines).strip()

    if not text:
        return {
            "ok": False,
            "error": "OCR_NO_TEXT",
            "message": "OCR produced no text.",
            "backend": "paddleocr",
            "text": "",
            "line_count": 0,
        }

    return {
        "ok": True,
        "backend": "paddleocr",
        "text": text,
        "line_count": len(lines),
    }


def _handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    if request.get("shutdown", False):
        return {"ok": True, "backend": "paddleocr", "shutdown": True}

    input_value = request.get("input")
    if not input_value:
        return {"ok": False, "error": "INVALID_REQUEST", "message": "missing input"}

    input_path = Path(input_value)
    if not input_path.is_file():
        return {
            "ok": False,
            "error": "INPUT_NOT_FOUND",
            "message": f"Input image file not found: {input_path}",
        }

    try:
        return _recognize_image(
            input_path=input_path,
            language=request.get("lang", "ch"),
            use_doc_orientation_classify=request.get(
                "use_doc_orientation_classify", False
            ),
            use_doc_unwarping=request.get("use_doc_unwarping", False),
            use_textline_orientation=request.get("use_textline_orientation", False),
        )
    except RuntimeError as e:
        return {"ok": False, "error": "OCR_BACKEND_UNAVAILABLE", "message": str(e)}
    except Exception as e:
        return {
            "ok": False,
            "error": "OCR_FAILED",
            "message": f"{e.__class__.__name__}: {e}",
        }


def _run_protocol() -> int:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue

        try:
            request = json.loads(stripped)
        except json.JSONDecodeError as e:
            _write_response({"ok": False, "error": "INVALID_JSON", "message": str(e)})
            continue

        response = _handle_request(request)
        _write_response(response)

        if response.get("shutdown"):
            return 0

    return 0


def _run_single(args: argparse.Namespace) -> int:
    request = {
        "input": args.input,
        "lang": args.lang,
        "use_doc_orientation_classify": args.use_doc_orientation_classify,
        "use_doc_unwarping": args.use_doc_unwarping,
        "use_textline_orientation": args.use_textline_orientation,
    }
    _write_response(_handle_request(request))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="")
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--use-doc-orientation-classify", action="store_true")
    parser.add_argument("--use-doc-unwarping", action="store_true")
    parser.add_argument("--use-textline-orientation", action="store_true")
    return parser.parse_args()


def main() -> int:
    sys.stdout = sys.stderr
    args = _parse_args()
    if args.input:
        return _run_single(args)
    return _run_protocol()


if __name__ == "__main__":
    raise SystemExit(main())
