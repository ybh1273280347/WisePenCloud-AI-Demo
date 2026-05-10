对，这个问题你说得对。当前这版里有不少方法**没有使用类内状态**，硬塞进类里会让 `NativeDocumentParser` / `DocumentParser` 看起来像“函数容器”。应该拆成：

```text id="0cwvnz"
有状态对象：
    DocumentParser
    DoclingDocumentParser

无状态辅助函数：
    detect_doc_type
    parse_pdf_native
    parse_docx_native
    parse_xlsx_native
    parse_pptx_native
    finalize_document_text
    log_quality_warnings
```

`NativeDocumentParser` 这个类其实没必要存在。它只是维护了一个 parser dict，而 parser 函数本身没有用 `self`。

---

# 建议重构方向

## 保留类

### `DocumentParser`

保留，因为它有状态：

```python
self._min_content_length
self._max_document_size
self._docling_parser
```

### `DoclingDocumentParser`

保留，因为它有状态：

```python
self._available
self._converter
```

---

## 移出类的方法

这些都可以变成模块级函数：

```text
NativeDocumentParser._parse_pdf      -> parse_pdf_native
NativeDocumentParser._parse_docx     -> parse_docx_native
NativeDocumentParser._parse_xlsx     -> parse_xlsx_native
NativeDocumentParser._parse_pptx     -> parse_pptx_native
DocumentParser._detect_doc_type      -> detect_doc_type
DocumentParser._log_quality_warnings -> log_quality_warnings
```

`DocumentParser._finalize()` 也可以移出，但它依赖 `self._min_content_length`。可以改成模块函数：

```python
finalize_document_text(
    text,
    doc_type=doc_type,
    parser=parser,
    min_content_length=self._min_content_length,
)
```

---

# 推荐结构

```python
NATIVE_PARSERS = {
    "pdf": parse_pdf_native,
    "docx": parse_docx_native,
    "xlsx": parse_xlsx_native,
    "pptx": parse_pptx_native,
}
```

然后 `DocumentParser.parse()` 里：

```python
text = parse_native_document(data, doc_type)
```

即可。

---

# 给 Codex 的修正提示词

```text
请重构 document_parser.py，目标是减少无状态方法塞进类里造成的膨胀。

原则：
- 使用类内状态的方法保留在类里。
- 不使用 self 的解析函数移动到模块级函数。
- 不改变 DocumentParser 对外 API。
- 不改变 Docling 主路径 + native 兼容 fallback 语义。
- 不引入新的解析器架构。

具体修改：

1. 删除 NativeDocumentParser 类。
   这个类只是维护 parser dict，内部 _parse_pdf/_parse_docx/_parse_xlsx/_parse_pptx 都没有使用实例状态，不需要作为类存在。

2. 将 NativeDocumentParser 内的方法改为模块级函数：
   - parse_pdf_native(data: bytes) -> Optional[str]
   - parse_docx_native(data: bytes) -> Optional[str]
   - parse_xlsx_native(data: bytes) -> Optional[str]
   - parse_pptx_native(data: bytes) -> Optional[str]

3. 新增模块级常量：
   NATIVE_PARSERS: Dict[str, Callable[[bytes], Optional[str]]] = {
       "pdf": parse_pdf_native,
       "docx": parse_docx_native,
       "xlsx": parse_xlsx_native,
       "pptx": parse_pptx_native,
   }

4. 新增模块级函数：
   def parse_native_document(data: bytes, doc_type: str) -> Optional[str]:
       parser = NATIVE_PARSERS.get(doc_type)
       if parser is None:
           log_fail("Native 文档解析", f"不支持的文档类型: {doc_type}")
           return None
       return parser(data)

5. 将 DocumentParser._detect_doc_type 移为模块级函数：
   detect_doc_type(data: bytes) -> Optional[str]

   DocumentParser.parse() 中改为：
   doc_type = detect_doc_type(data)

6. 将 DocumentParser._log_quality_warnings 移为模块级函数：
   log_quality_warnings(text: str, *, doc_type: str, parser: str) -> None

7. 将 DocumentParser._finalize 移为模块级函数：
   finalize_document_text(
       text: str,
       *,
       doc_type: str,
       parser: str,
       min_content_length: int,
   ) -> Optional[str]

   内部逻辑保持原样：
   - normalize_text
   - 长度阈值判断
   - log_quality_warnings
   - log_ok("文档清洗", ...)

8. DocumentParser.__init__ 中删除：
   self._native_parser = NativeDocumentParser()

   只保留：
   self._min_content_length
   self._max_document_size
   self._docling_parser = DoclingDocumentParser()

9. DocumentParser.parse() 中 native 调用改为：
   text = parse_native_document(data, doc_type)

10. DocumentParser.parse() 语义保持：
   - 文件过大直接 None
   - detect_doc_type 失败直接 None
   - xlsx 直接走 native
   - pdf/docx/pptx 优先 Docling
   - Docling 失败且 settings.DOCUMENT_PARSER_ENABLE_NATIVE_FALLBACK=True 时走 native
   - 所有返回前统一调用 finalize_document_text

11. DoclingDocumentParser 保持类。
   因为它管理 self._available 和 self._converter，属于有状态对象。

12. __all__ 仍然只导出：
   __all__ = ["DocumentParser"]

13. 不要做：
   - 不要重新引入 NativeDocumentParser 类
   - 不要把所有函数拆到其他文件
   - 不要引入 Unstructured
   - 不要改变 parse / parse_async 的对外行为
   - 不要改变日志语义
```

---

# 重构后的核心形态应该像这样

```python
class DocumentParser:

    def __init__(
        self,
        min_content_length: int = 50,
        max_document_size: int = 50 * 1024 * 1024,
    ):
        self._min_content_length = min_content_length
        self._max_document_size = max_document_size
        self._docling_parser = DoclingDocumentParser()

    def parse(self, data: bytes) -> Optional[str]:
        if len(data) > self._max_document_size:
            log_fail("文档解析", f"文件过大({len(data)}字节)，上限{self._max_document_size}字节")
            return None

        doc_type = detect_doc_type(data)
        if doc_type is None:
            return None

        if doc_type == "xlsx":
            text = parse_native_document(data, doc_type)
            return finalize_document_text(
                text,
                doc_type=doc_type,
                parser="native",
                min_content_length=self._min_content_length,
            ) if text else None

        if self._docling_parser.available and doc_type in DOCLING_DOC_TYPES:
            text = self._docling_parser.parse(data, doc_type)
            if text:
                return finalize_document_text(
                    text,
                    doc_type=doc_type,
                    parser="docling",
                    min_content_length=self._min_content_length,
                )

            if not settings.DOCUMENT_PARSER_ENABLE_NATIVE_FALLBACK:
                return None

        text = parse_native_document(data, doc_type)
        if text:
            return finalize_document_text(
                text,
                doc_type=doc_type,
                parser="native",
                min_content_length=self._min_content_length,
            )

        return None

    async def parse_async(self, data: bytes) -> Optional[str]:
        return await asyncio.to_thread(self.parse, data)
```

---

# 进一步小建议

## 1. `NATIVE_PARSERS` 要放在函数定义之后

因为它引用：

```python
parse_pdf_native
parse_docx_native
parse_xlsx_native
parse_pptx_native
```

所以顺序应是：

```text
parse_pdf_native
parse_docx_native
parse_xlsx_native
parse_pptx_native
NATIVE_PARSERS
parse_native_document
```

---

## 2. `normalize_text` 保留模块级函数

这个是合理的，无状态，已经在模块级。

---

## 3. `DoclingDocumentParser.parse()` 可以保留类方法

因为它依赖：

```python
self._available
self._converter
```

这是真正有类内状态的地方。

---

# 结论

你的代码风格判断是正确的：

```text
类不是命名空间；
不用 self 的函数不该塞进类里。
```

这次应该把 `NativeDocumentParser` 整个拆掉，让 `DocumentParser` 只承担“有状态协调入口”的角色，native 解析全部变成模块级函数。这样文件会更清楚，后续也更容易测试单个 parser 函数。
