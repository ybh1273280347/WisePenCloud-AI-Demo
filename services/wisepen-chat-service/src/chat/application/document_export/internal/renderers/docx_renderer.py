import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List, Optional

from ...errors import ExportDependencyMissingError, ExportRenderError, ExportTimeoutError
from ...models import ExportRequest
from .base import DocumentRenderer

_PANDOC_STDERR_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class DocxRenderer(DocumentRenderer):
    pandoc_bin: str = "pandoc"
    target_format: str = "docx"

    async def render(self, request: ExportRequest) -> None:
        if shutil.which(self.pandoc_bin) is None:
            raise ExportDependencyMissingError("pandoc")

        with TemporaryDirectory(prefix="document-export-docx-") as tmp_dir:
            input_path = Path(tmp_dir) / "input.md"
            await asyncio.to_thread(
                input_path.write_text,
                request.markdown,
                encoding="utf-8",
                newline="\n",
            )

            self._validate_reference_docx(request)
            args = self._build_pandoc_args(input_path=input_path, request=request)

            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(input_path.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                _, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=request.options.timeout_seconds,
                )
            except asyncio.TimeoutError as e:
                process.kill()
                _, stderr = await process.communicate()
                message = self._format_stderr(stderr)
                raise ExportTimeoutError(
                    "DocxRenderer timed out for target "
                    f"{request.output_path}: {message}"
                ) from e

            if process.returncode != 0:
                message = self._format_stderr(stderr)
                raise ExportRenderError(
                    "DocxRenderer failed for target "
                    f"{request.output_path}: Pandoc exited with code "
                    f"{process.returncode}. {message}"
                )

    def _build_pandoc_args(
        self, *, input_path: Path, request: ExportRequest
    ) -> List[str]:
        args = [
            self.pandoc_bin,
            str(input_path),
            "--from=gfm",
            "--to=docx",
            "--output",
            str(request.output_path),
        ]

        if request.options.reference_docx is not None:
            args.extend(["--reference-doc", str(request.options.reference_docx)])

        resource_paths = self._stable_resource_paths(
            [input_path.parent, request.options.assets_dir]
        )
        args.extend(["--resource-path", os.pathsep.join(resource_paths)])
        return args

    def _stable_resource_paths(self, paths: List[Optional[Path]]) -> List[str]:
        resource_paths: List[str] = []
        seen = set()
        for path in paths:
            if path is None:
                continue
            value = str(path)
            if value in seen:
                continue
            seen.add(value)
            resource_paths.append(value)
        return resource_paths

    def _validate_reference_docx(self, request: ExportRequest) -> None:
        reference_docx = request.options.reference_docx
        if reference_docx is None:
            return
        if not reference_docx.is_file():
            raise ExportRenderError(
                "DocxRenderer failed for target "
                f"{request.output_path}: reference_docx is not a file: "
                f"{reference_docx}"
            )

    def _format_stderr(self, stderr: bytes) -> str:
        message = stderr.decode("utf-8", errors="replace").strip()
        if not message:
            return "No stderr output."
        if len(message) <= _PANDOC_STDERR_LIMIT:
            return message
        return message[:_PANDOC_STDERR_LIMIT] + "..."
