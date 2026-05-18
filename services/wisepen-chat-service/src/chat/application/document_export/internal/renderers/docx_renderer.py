import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List

from ...errors import ExportDependencyMissingError, ExportRenderError, ExportTimeoutError
from ...models import ExportRequest
from .base import DocumentRenderer


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

            resource_paths: List[str] = [str(input_path.parent)]
            if request.options.assets_dir is not None:
                resource_paths.append(str(request.options.assets_dir))
            args.extend(["--resource-path", os.pathsep.join(resource_paths)])

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
                await process.communicate()
                raise ExportTimeoutError("Pandoc DOCX rendering timed out.") from e

            if process.returncode != 0:
                message = stderr.decode("utf-8", errors="replace")[:1000]
                raise ExportRenderError(f"Pandoc DOCX rendering failed: {message}")
