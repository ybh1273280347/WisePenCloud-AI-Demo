import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


_PROTOCOL_STDOUT = sys.stdout
_ENGINES: Dict[Tuple[str, bool, bool, bool], Any] = {}


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
) -> Any:
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
            from paddleocr import PaddleOCR
    except ImportError as e:
        raise RuntimeError(str(e)) from e

    kwargs = {
        "lang": language,
        "use_doc_orientation_classify": use_doc_orientation_classify,
        "use_doc_unwarping": use_doc_unwarping,
        "use_textline_orientation": use_textline_orientation,
    }

    with _redirect_stdout_to_stderr():
        engine = PaddleOCR(**kwargs)

    _ENGINES[key] = engine
    return engine


def _extend_strings(parts: List[str], values: Iterable[Any]) -> None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                parts.append(text)


def _collect_texts(value: Any, parts: List[str], *, depth: int = 0) -> None:
    if depth > 8 or value is None:
        return

    if isinstance(value, str):
        text = value.strip()
        if text:
            parts.append(text)
        return

    if isinstance(value, dict):
        preferred_keys = ("rec_texts", "text", "transcription", "texts")
        handled_keys = set()

        for key in preferred_keys:
            if key in value:
                handled_keys.add(key)
                nested = value[key]
                if isinstance(nested, list):
                    _extend_strings(parts, nested)
                else:
                    _collect_texts(nested, parts, depth=depth + 1)

        for key, nested in value.items():
            if key not in handled_keys:
                _collect_texts(nested, parts, depth=depth + 1)
        return

    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            second = value[1]
            if isinstance(second, str):
                text = second.strip()
                if text:
                    parts.append(text)
            elif isinstance(second, (list, tuple)) and second and isinstance(second[0], str):
                text = second[0].strip()
                if text:
                    parts.append(text)

        for item in value:
            _collect_texts(item, parts, depth=depth + 1)
        return

    for attr in ("rec_texts", "text", "texts", "data"):
        if hasattr(value, attr):
            try:
                _collect_texts(getattr(value, attr), parts, depth=depth + 1)
            except Exception:
                pass

    if hasattr(value, "json"):
        try:
            _collect_texts(value.json(), parts, depth=depth + 1)
        except Exception:
            pass


def _normalize_lines(parts: List[str]) -> List[str]:
    lines: List[str] = []
    seen = set()

    for part in parts:
        text = str(part).strip()
        if not text or text in seen:
            continue
        lines.append(text)
        seen.add(text)

    return lines


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
        raw_result = engine.predict(input=str(input_path))

    parts: List[str] = []
    _collect_texts(raw_result, parts)
    lines = _normalize_lines(parts)
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
            use_doc_orientation_classify=request.get("use_doc_orientation_classify", False),
            use_doc_unwarping=request.get("use_doc_unwarping", False),
            use_textline_orientation=request.get("use_textline_orientation", False),
        )
    except RuntimeError as e:
        return {"ok": False, "error": "OCR_BACKEND_UNAVAILABLE", "message": str(e)}
    except Exception as e:
        return {"ok": False, "error": "OCR_FAILED", "message": f"{e.__class__.__name__}: {e}"}


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
