"""Static HTML text, not a browser: never run scripts or fetch resources."""

from html.parser import HTMLParser
import re

from .contracts import ContractError

EXTRACT_VERSION = "html-2"
EXTRACT_VERSIONS = ("html-1", "html-2")
LIMITATION = "仅提取静态HTML文字，不执行脚本或读取外部资源；动态内容及显示状态未核验。"
MAX_NODES = 100_000
MAX_DEPTH = 256
MAX_CHARACTERS = 8_000_000


class StaticHTMLReader(HTMLParser):
    VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                      "param", "source", "track", "wbr"))
    EXCLUDED = frozenset(("script", "style", "noscript", "template", "svg", "canvas",
                          "iframe", "object", "embed"))
    BLOCK = frozenset(("p", "div", "section", "article", "header", "footer", "main", "aside",
                       "nav", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre",
                       "h1", "h2", "h3", "h4", "h5", "h6", "title", "caption"))
    TITLES = frozenset(("title", "h1", "h2", "h3", "h4", "h5", "h6"))
    METADATA_FIELDS = frozenset(("articletitle", "pubdate", "contentsource", "author", "datepublished",
        "datemodified", "article:published_time", "article:modified_time", "og:title",
        "dc.title", "dc.date.issued", "dc.date.modified", "citation_publication_date"))

    def __init__(self, *, include_metadata=False):
        super().__init__(convert_charrefs=True)
        self.stack, self.buffer, self.parts = [], [], []
        self.nodes = self.characters = 0
        self.table = False
        self.kind = "段落"
        self.counts = {}
        self.include_metadata = include_metadata
        self.metadata_parts = []

    def hidden(self):
        return any(tag in self.EXCLUDED for tag in self.stack) or (
            "head" in self.stack and "title" not in self.stack)

    def flush(self, kind=None):
        text = "".join(self.buffer).strip()
        self.buffer.clear()
        if text:
            kind = kind or self.kind
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.parts.append({"locator": kind + str(self.counts[kind]), "text": text})
        self.kind = "段落"

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ContractError("HTML节点超过解析限制")
        if tag not in self.VOID:
            self.stack.append(tag)
            if len(self.stack) > MAX_DEPTH:
                raise ContractError("HTML嵌套超过解析限制")
        if self.include_metadata and tag == 'meta' and 'head' in self.stack and not any(
                excluded in self.stack for excluded in self.EXCLUDED):
            attributes = dict(attrs)
            field = attributes.get('name') or attributes.get('property')
            content = attributes.get('content')
            if (isinstance(field, str) and field.casefold() in self.METADATA_FIELDS
                    and isinstance(content, str) and content.strip()):
                # Keep the actual tag, not a translated label or inferred date.
                text = self.get_starttag_text()
                self.characters += len(text)
                if self.characters > MAX_CHARACTERS:
                    raise ContractError("HTML文字超过解析限制")
                self.metadata_parts.append(dict(locator='元数据' + str(len(self.metadata_parts) + 1),
                    text=text, metadata_field=field))
        if self.hidden() or tag in self.EXCLUDED:
            return
        if tag == "table":
            if self.table:
                raise ContractError("嵌套HTML表格需单独整理，未采用正文")
            self.flush()
            self.table = True
        elif not self.table and tag in self.BLOCK:
            self.flush()
            if tag in self.TITLES:
                self.kind = "标题"
        elif tag == "br":
            self.buffer.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.hidden():
            if self.table and tag in ("td", "th"):
                self.buffer.append("\t")
            elif self.table and tag == "tr":
                self.buffer.append("\n")
            elif self.table and tag == "table":
                self.flush("表")
                self.table = False
            elif not self.table and tag in self.BLOCK:
                self.flush()
        if tag in self.stack:
            position = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[position:]

    def handle_data(self, data):
        if self.hidden():
            return
        self.characters += len(data)
        if self.characters > MAX_CHARACTERS:
            raise ContractError("HTML文字超过解析限制")
        # Collapse only HTML whitespace. Entities are decoded by HTMLParser;
        # spans remain contiguous when the source contains no intervening space.
        normalized = re.sub(r"[ \t\r\n\f]+", " ", data)
        if self.buffer or normalized.strip():
            self.buffer.append(normalized)


def extract_html(payload, *, extract_version=None):
    version = EXTRACT_VERSION if extract_version is None else extract_version
    if version not in EXTRACT_VERSIONS:
        raise ContractError('HTML提取版本未登记，不改写留存正文')
    text = payload.decode("utf-8-sig")  # No guessed encoding or replacement bytes.
    reader = StaticHTMLReader(include_metadata=version == 'html-2')
    reader.feed(text)
    reader.close()
    reader.flush("表" if reader.table else None)
    return reader.metadata_parts + reader.parts
