"""
PaddlePaddle OneDNN fused_conv2d 运行时诊断脚本

目标：定位 test_ocr_unit.py 中图片 OCR / PDF OCR / worker 复用失败根因。

使用方式：
    uv run python test/debug_paddleocr_runtime.py
"""
from __future__ import annotations

import os
import platform
import sys
import traceback as tb


# ── 第 0 步：在 import paddle/torch 前设置实验环境变量 ──────────────────────
_EXPERIMENTAL_FLAGS = {
    "FLAGS_use_mkldnn": "0",
    "FLAGS_enable_onednn": "0",
    "FLAGS_cpu_math_library_num_threads": "1",
}

# 额外尝试关闭 oneDNN 的 workaround
_ADDITIONAL_FLAGS = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "DNNL_MAX_CPU_ISA": "NONE",
}

print("=" * 60)
print("PaddleOCR 运行时诊断")
print("=" * 60)

print(f"\nPython version: {sys.version}")
print(f"Platform: {platform.platform()}")
print(f"Machine: {platform.machine()}")

for key, val in _EXPERIMENTAL_FLAGS.items():
    old = os.environ.get(key, "(not set)")
    os.environ[key] = val
    print(f"  {key}: {old} -> {val}")

for key, val in _ADDITIONAL_FLAGS.items():
    old = os.environ.get(key, "(not set)")
    os.environ[key] = val
    print(f"  {key}: {old} -> {val}")


# ── 第 1 步：试导入 torch ──────────────────────────────────────────────────
print(f"\n--- torch ---")
try:
    import torch
    print(f"  import: OK")
    print(f"  torch.__version__: {torch.__version__}")
    print(f"  torch.__file__: {torch.__file__}")
except Exception as e:
    print(f"  import: FAIL -> {e}")
    tb.print_exc()


# ── 第 2 步：试导入 paddle ─────────────────────────────────────────────────
print(f"\n--- paddle ---")
try:
    import paddle
    print(f"  import: OK")
    print(f"  paddle.__version__: {paddle.__version__}")
    try:
        dev = paddle.device.get_device()
        print(f"  paddle.device.get_device(): {dev}")
    except Exception as e:
        print(f"  get_device(): FAIL -> {e}")

    try:
        cuda = paddle.is_compiled_with_cuda()
        print(f"  compiled_with_cuda: {cuda}")
    except Exception:
        print(f"  compiled_with_cuda: N/A (no API)")

    # 打印 oneDNN / MKLDNN 相关状态
    try:
        from paddle.fluid import core
        print(f"  is_compiled_with_mkldnn: {core.is_compiled_with_mkldnn()}")
    except Exception:
        print(f"  is_compiled_with_mkldnn: N/A")

except Exception as e:
    print(f"  import: FAIL -> {e}")
    tb.print_exc()
    print("\n诊断结论：paddle 本身无法导入，问题不在 oneDNN。")
    sys.exit(1)


# ── 第 3 步：试导入 PaddleOCR ──────────────────────────────────────────────
print(f"\n--- PaddleOCR ---")
try:
    with open(os.devnull, "w") as silent:
        old_stdout = sys.stdout
        sys.stdout = silent
        from paddleocr import PaddleOCR
        sys.stdout = old_stdout
    print(f"  import: OK")
    print(f"  PaddleOCR.__module__: {PaddleOCR.__module__}")
except Exception as e:
    sys.stdout = sys.__stdout__
    print(f"  import: FAIL -> {e}")
    tb.print_exc()
    print("\n诊断结论：PaddleOCR 导入失败，无法进行推理诊断。")
    sys.exit(1)


# ── 第 4 步：生成测试图片 ──────────────────────────────────────────────────
print(f"\n--- 生成测试图片 ---")
try:
    from PIL import Image, ImageDraw, ImageFont
    import io

    img = Image.new("RGB", (400, 100), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 30), "OCR TEST 123", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    print(f"  PNG 尺寸: {len(png_bytes)} bytes, {img.size}")
except Exception as e:
    print(f"  生成失败 -> {e}")
    tb.print_exc()
    sys.exit(1)


# ── 第 5 步：初始化 PaddleOCR ──────────────────────────────────────────────
print(f"\n--- PaddleOCR 初始化 ---")
ocr: PaddleOCR = None
try:
    with open(os.devnull, "w") as silent:
        old_stdout = sys.stdout
        sys.stdout = silent
        ocr = PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        sys.stdout = old_stdout
    print(f"  初始化: OK")
except Exception as e:
    sys.stdout = sys.__stdout__
    print(f"  初始化: FAIL -> {e}")
    tb.print_exc()
    print("\n诊断结论：PaddleOCR 初始化失败，无法进行推理诊断。")
    sys.exit(1)


# ── 第 6 步：运行推理 ──────────────────────────────────────────────────────
print(f"\n--- PaddleOCR 推理 ---")
try:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="wisepen-ocr-dbg-") as tmp_dir:
        img_path = os.path.join(tmp_dir, "test.png")
        with open(img_path, "wb") as f:
            f.write(png_bytes)
        print(f"  临时图片: {img_path}")

        with open(os.devnull, "w") as silent:
            old_stdout = sys.stdout
            sys.stdout = silent
            result = ocr.ocr(img_path, cls=False)
            sys.stdout = old_stdout

        print(f"  推理: OK")
        print(f"  结果: {result}")
        if result and result[0]:
            texts = [line[1][0] for box_group in result for line in (box_group if box_group else [])]
            print(f"  识别文本: {texts}")
        else:
            print(f"  警告：OCR 返回空结果，可能是识别失败")
except Exception as e:
    sys.stdout = sys.__stdout__
    print(f"  推理: FAIL -> {e}")
    tb.print_exc()
    print("\n诊断结论：PaddleOCR 推理时出错，根因就在此。")
    sys.exit(1)


# ── 总结 ────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 60}")
print(f"诊断通过")
print(f"{'=' * 60}")
print(f"  PaddlePaddle runtime 在禁用 oneDNN 的环境变量下可正常推理。")
print(f"  如果之前设置 FLAGS_use_mkldnn=0 / FLAGS_enable_onednn=0 后通过，")
print(f"  则修复方向为：在 LocalOcrProcessor._start_worker() 的 worker env 中设置这些 flags。")
sys.exit(0)