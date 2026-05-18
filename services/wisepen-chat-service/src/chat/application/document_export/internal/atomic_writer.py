import asyncio
from pathlib import Path
from uuid import uuid4

from ..errors import ExportOutputError


class AtomicExportWriter:
    async def write_with_renderer(self, *, output_path: Path, render) -> None:
        await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
        tmp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")

        try:
            await render(tmp_path)

            is_file = await asyncio.to_thread(tmp_path.is_file)
            if not is_file:
                raise ExportOutputError("Export failed: output file was not created.")

            size = await asyncio.to_thread(lambda: tmp_path.stat().st_size)
            if size <= 0:
                raise ExportOutputError("Export failed: output file is empty.")

            await asyncio.to_thread(tmp_path.replace, output_path)
        finally:
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
