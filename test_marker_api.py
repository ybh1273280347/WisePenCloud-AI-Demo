import time
from pathlib import Path

from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered

pdf_path = Path("/tmp/test.pdf")

print("=== build models/converter ===")
t0 = time.perf_counter()

converter = PdfConverter(
    artifact_dict=create_model_dict(),
)

t1 = time.perf_counter()
print(f"build_converter_seconds={t1 - t0:.3f}")

print("=== first convert ===")
t2 = time.perf_counter()

rendered = converter(str(pdf_path))

t3 = time.perf_counter()
text, _, images = text_from_rendered(rendered)
t4 = time.perf_counter()

print(f"first_convert_seconds={t3 - t2:.3f}")
print(f"first_text_from_rendered_seconds={t4 - t3:.3f}")
print(f"text_len={len(text)}")
print(f"image_count={len(images)}")
print(f"metadata_keys={list(rendered.metadata.keys())}")
print("page_stats=", rendered.metadata.get("page_stats"))

print("=== second convert, same converter ===")
t5 = time.perf_counter()

rendered2 = converter(str(pdf_path))

t6 = time.perf_counter()
text2, _, images2 = text_from_rendered(rendered2)
t7 = time.perf_counter()

print(f"second_convert_seconds={t6 - t5:.3f}")
print(f"second_text_from_rendered_seconds={t7 - t6:.3f}")
print(f"text_len={len(text2)}")
print(f"image_count={len(images2)}")
print(f"metadata_keys={list(rendered2.metadata.keys())}")
print("page_stats=", rendered2.metadata.get("page_stats"))
