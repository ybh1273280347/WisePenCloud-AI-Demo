import asyncio
import json
from typing import Any, Dict

from chat.application.tools.text.count_text_chars_tool import (
    CountTextCharsTool,
    count_text_chars,
    strip_markdown_to_text,
)


def print_case(title: str, text: str, result: Dict[str, Any]) -> None:
    print("=" * 80)
    print(title)
    print("- input:")
    print(text)
    print("- result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()


async def main() -> None:
    tool = CountTextCharsTool()

    text = "这是一段中文。"
    print_case("1. 普通中文", text, count_text_chars(text))

    text = "今天学习 Transformer architecture 的 self-attention 机制。"
    result = count_text_chars(text)
    print_case("2. 中英混排", text, result)
    print(
        "natural_text_count == cjk_character_count + word_count:",
        result["natural_text_count"],
        "==",
        result["cjk_character_count"],
        "+",
        result["word_count"],
    )
    print()

    text = "# 标题\n\n这是 **加粗** 文本，[链接](https://example.com)。"
    counted_text = strip_markdown_to_text(text)
    print_case(
        "3. Markdown 默认剥离格式",
        text,
        {
            "counted_text": counted_text,
            **count_text_chars(counted_text),
        },
    )

    text = "中文， English!\nNext line."
    print_case("4. 空白和标点", text, count_text_chars(text))

    text = "# 标题\n\n这是 **加粗** 文本"
    output = await tool.execute({}, text=text, strip_markdown=False)
    print("=" * 80)
    print("5. strip_markdown=False 原始 Markdown 统计")
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
