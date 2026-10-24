"""Read local text documents without executing markup or resolving resources.

DOCX is inspected as a bounded ZIP package; HTML/Markdown only load images
embedded in the upload itself. No parser in this module reads paths or URLs.
Private ``_assets`` bytes are returned to the importer for local PNG storage.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from html import escape, unescape
from html.parser import HTMLParser
import io
from pathlib import PurePosixPath
import re
import struct
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile

import fitz

from .pdf_parser import MAX_PARAGRAPH_CHARS, MAX_TEXT_CHARS, PARSE_VERSION, _build_outline
from .pdf_runtime import PDF_LOCK


DOCUMENT_PARSE_VERSION = 1
FORMATS = {
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".md": ("markdown", "text/markdown"), ".markdown": ("markdown", "text/markdown"),
    ".txt": ("txt", "text/plain"), ".html": ("html", "text/html"), ".htm": ("html", "text/html"),
}
MAX_XML_BYTES = 12 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_ASSETS = 128
MAX_ASSET_BYTES = 50 * 1024 * 1024


class DocumentExtractionError(ValueError):
    """The uploaded local document is unsupported, corrupt, or too large."""


def document_format(filename: str) -> str | None:
    info = FORMATS.get(PurePosixPath(filename.replace("\\", "/")).suffix.lower())
    return info[0] if info else None


def _text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif data.startswith(b"\xef\xbb\xbf"):
        encodings = ("utf-8-sig",)
    else:
        encodings = ("utf-8", "gb18030")
    for encoding in encodings:
        try:
            result = data.decode(encoding)
        except UnicodeError:
            continue
        if any(ord(c) < 32 and c not in "\t\r\n\f" for c in result):
            raise DocumentExtractionError("文件包含二进制内容，请选择有效的文本、Markdown 或 HTML 文档。")
        if len(result) > MAX_TEXT_CHARS:
            raise DocumentExtractionError("文档文字过长，请按章节拆分后导入。")
        return result.replace("\r\n", "\n").replace("\r", "\n")
    raise DocumentExtractionError("无法识别文档编码，请另存为 UTF-8、UTF-16 或 GB18030 后导入。")


def _xml(data: bytes) -> ET.Element:
    # ElementTree accepts UTF-16/32 XML as well as UTF-8. Removing NUL code
    # units makes the ASCII declaration keywords visible in those encodings
    # before the XML parser can expand any internal entities.
    if len(data) > MAX_XML_BYTES or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data.replace(b"\x00", b""), re.I):
        raise DocumentExtractionError("DOCX 的 XML 内容过大或包含不支持的实体声明。")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise DocumentExtractionError("DOCX 内部 XML 已损坏，请重新保存后导入。") from None


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Check encoded raster dimensions before asking MuPDF to allocate pixels."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 7:
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return width, height
            offset += length
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        if kind == b"VP8L" and data[20] == 0x2F:
            value = int.from_bytes(data[21:25], "little")
            return (value & 0x3FFF) + 1, ((value >> 14) & 0x3FFF) + 1
        if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
    return None


def _png(data: bytes) -> tuple[bytes, int, int] | None:
    if len(data) > MAX_IMAGE_BYTES:
        return None
    size = _image_dimensions(data)
    if size is None or not all(0 < side <= 16384 for side in size) or size[0] * size[1] > MAX_IMAGE_PIXELS:
        return None
    try:
        with PDF_LOCK:
            image = fitz.Pixmap(data)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                return None
            if image.colorspace and image.colorspace.n > 3:
                image = fitz.Pixmap(fitz.csRGB, image)
            result = image.tobytes("png")
            if len(result) > MAX_IMAGE_BYTES:
                return None
            return result, image.width, image.height
    except Exception:
        return None


@dataclass
class _Builder:
    filename: str
    paragraphs: list[dict] = field(default_factory=list)
    assets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: str = ""
    chars: int = 0
    asset_bytes: int = 0

    def warn(self, message: str):
        if message not in self.warnings:
            self.warnings.append(message)

    def paragraph(self, value: str, level: int = 0, *, literal: bool = False):
        value = value.strip() if literal else re.sub(r"\s+", " ", value).strip()
        if not value:
            return
        self.chars += len(value)
        if self.chars > MAX_TEXT_CHARS:
            raise DocumentExtractionError("文档文字过长，请按章节拆分后导入。")
        while value:
            cut = len(value)
            if cut > MAX_PARAGRAPH_CHARS:
                cut = value.rfind(" ", MAX_PARAGRAPH_CHARS // 2, MAX_PARAGRAPH_CHARS)
                if cut < 0:
                    cut = MAX_PARAGRAPH_CHARS
                self.warn("个别超长段落已分段，请对照源文件核对跨段公式。")
            part, value = value[:cut].strip(), value[cut:].strip()
            pid = f"p-{len(self.paragraphs) + 1}"
            self.paragraphs.append({"id": pid, "text": part, "page": 1,
                                    "kind": "heading" if level else "paragraph", "level": level,
                                    "translation": None, "error": None, "section_id": ""})
            for asset in self.assets:
                if asset["before_paragraph_id"] is None:
                    asset["before_paragraph_id"] = pid

    def image(self, data: bytes | None, caption: str = ""):
        if data is None:
            self.warn("外链或本机路径图片未读取。请使用 DOCX 内嵌图片，或在 Markdown/HTML 中嵌入 data URI 图片。")
            return
        if len(self.assets) >= MAX_ASSETS or self.asset_bytes + len(data) > MAX_ASSET_BYTES:
            self.warn("文档内嵌图片较多或过大，部分图片未导入。")
            return
        converted = _png(data)
        if converted is None:
            self.warn("部分图片格式不支持、损坏或尺寸过大，未导入该图片。支持 PNG、JPEG、GIF 和 WebP 栅格图片。")
            return
        content, width, height = converted
        if self.asset_bytes + len(content) > MAX_ASSET_BYTES:
            self.warn("文档内嵌图片较多或过大，部分图片未导入。")
            return
        self.asset_bytes += len(content)
        self.assets.append({"id": f"f-{len(self.assets) + 1}", "data": content,
                            "width": width, "height": height, "page": 1,
                            "after_paragraph_id": self.paragraphs[-1]["id"] if self.paragraphs else None,
                            "before_paragraph_id": None, "caption": caption[:1000], "text_paragraph_ids": []})

    def finish(self, kind: str, media: str) -> dict:
        if not self.paragraphs:
            raise DocumentExtractionError("没有提取到可阅读的正文，请确认文件包含文字内容。")
        heading = next((p["text"] for p in self.paragraphs if p["kind"] == "heading"), "")
        title = self.title or heading or PurePosixPath(self.filename).stem or "未命名文档"
        return {"title": title[:300], "source_format": kind, "source_media_type": media,
                "page_count": 1, "paragraphs": self.paragraphs, "parse_version": PARSE_VERSION,
                "document_parse_version": DOCUMENT_PARSE_VERSION,
                "outline": _build_outline(self.paragraphs, []), "warnings": self.warnings,
                "figures": [], "_assets": self.assets}


def _data_image(source: str) -> bytes | None:
    match = re.fullmatch(r"data:image/(?:png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\s]+)", source, re.I)
    if not match or len(match[1]) > (MAX_IMAGE_BYTES * 4 // 3 + 10):
        return None
    try:
        return base64.b64decode(re.sub(r"\s+", "", match[1]), validate=True)
    except (binascii.Error, ValueError):
        return None


class _HTMLReader(HTMLParser):
    BLOCKS = {"p", "div", "section", "article", "main", "header", "footer", "li", "ul", "ol", "blockquote", "tr", "table", "pre"}
    SKIP = {"script", "style", "iframe", "object", "embed", "svg", "noscript", "template"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, builder: _Builder):
        super().__init__(convert_charrefs=True)
        self.builder = builder
        self.buffer: list[str] = []
        self.skip: list[str] = []
        self.level = 0
        self.pre = 0
        self.in_head = False
        self.in_title = False
        self.title: list[str] = []

    def flush(self):
        self.builder.paragraph("".join(self.buffer), self.level, literal=bool(self.pre))
        self.buffer.clear()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if self.skip:
            if tag not in self.VOID:
                self.skip.append(tag)
            return
        hidden = "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true"
        hidden = hidden or bool(re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", attrs.get("style") or "", re.I))
        if tag in self.SKIP or hidden:
            if tag not in self.VOID:
                self.skip.append(tag)
            return
        if tag == "head":
            self.flush()
            self.in_head = True
        elif tag == "title":
            self.in_title = True
        elif self.in_head:
            return
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.level = int(tag[1])
        elif tag in self.BLOCKS:
            self.flush()
            if tag == "pre":
                self.pre += 1
        elif tag == "br":
            self.buffer.append("\n" if self.pre else " ")
        elif tag in ("td", "th"):
            if self.buffer:
                self.buffer.append(" | ")
        elif tag == "img":
            self.flush()
            self.builder.image(_data_image(attrs.get("src") or ""), unescape(attrs.get("alt") or ""))
        elif tag == "hr":
            self.flush()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("img", "br", "hr", "meta", "link", "input", "embed"):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.skip:
                del self.skip[len(self.skip) - 1 - self.skip[::-1].index(tag):]
            return
        if tag == "title":
            self.in_title = False
        elif tag == "head":
            self.in_head = False
        elif self.in_head:
            return
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.level = 0
        elif tag in self.BLOCKS:
            self.flush()
            if tag == "pre":
                self.pre = max(0, self.pre - 1)

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title.append(data)
        elif not self.in_head:
            self.buffer.append(data)


_MATH = re.compile(r"\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$|(?<!\\)\$(?!\$).*?(?<!\\)\$", re.S)


def _markdown_inline(text: str) -> str:
    math = []

    def save(match):
        math.append(match[0])
        return f"\x01{len(math) - 1}\x02"

    text = _MATH.sub(save, text)
    text = re.sub(r"\[([^\]]+)\]\([^\n]*?\)", r"\1", text)
    text = re.sub(r"(`+)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)(\*\*|__)(\S(?:.*?\S)?)\1(?!\w)", r"\2", text)
    text = re.sub(r"(?<!\w)(\*|_)(\S(?:.*?\S)?)\1(?!\w)", r"\2", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)
    text = re.sub(r"\x01(\d+)\x02", lambda m: math[int(m[1])], text)
    return unescape(text)


def _markdown(value: str, builder: _Builder):
    lines = value.splitlines()
    buffer = []
    fence = None
    math_fence = False
    index = 0

    def flush(*, literal=False):
        builder.paragraph(_markdown_inline("\n".join(buffer)), literal=literal)
        buffer.clear()

    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, min(100, len(lines))) if lines[i].strip() in ("---", "...")), None)
        if closing is not None:
            for line in lines[1:closing]:
                title = re.match(r"^title:\s*(.*?)\s*$", line, re.I)
                if title:
                    builder.title = title[1].strip("\"'")
            index = closing + 1
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        index += 1
        fenced = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            if fenced and fenced[1][0] == fence[0] and len(fenced[1]) >= len(fence):
                flush(literal=True)
                fence = None
            else:
                buffer.append(line)
            continue
        if fenced:
            flush()
            fence = fenced[1]
            continue
        if stripped == "$$" or stripped in (r"\[", r"\]"):
            if not math_fence:
                flush()
            buffer.append(line)
            math_fence = not math_fence
            if not math_fence:
                flush(literal=True)
            continue
        if math_fence:
            buffer.append(line)
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading:
            flush()
            builder.paragraph(_markdown_inline(heading[2]), len(heading[1]))
            continue
        if stripped and index < len(lines) and re.fullmatch(r"\s*(?:={3,}|-{3,})\s*", lines[index]):
            flush()
            builder.paragraph(_markdown_inline(line), 1 if "=" in lines[index] else 2)
            index += 1
            continue
        if not stripped or re.fullmatch(r"\s*(?:[-*_]\s*){3,}\s*", line):
            flush()
            continue
        images = list(re.finditer(r"!\[([^\]]*)\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+\"[^\"]*\")?\s*\)", line))
        if images:
            start = 0
            for image in images:
                buffer.append(line[start:image.start()])
                flush()
                builder.image(_data_image(image[2].strip("<>")), _markdown_inline(image[1]))
                start = image.end()
            buffer.append(line[start:])
            continue
        item = re.match(r"^\s*(?:[-+*]|\d+[.)])\s+(.*)$", line)
        if item:
            flush()
            builder.paragraph(_markdown_inline(item[1]))
        else:
            buffer.append(re.sub(r"^\s*>\s?", "", line))
    flush(literal=bool(fence or math_fence))


_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
       "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
       "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _word_value(element, namespace="w") -> str:
    return element.get("{" + _NS[namespace] + "}val", "") if element is not None else ""


def _omml(element, builder: _Builder) -> str:
    def child(name):
        found = element.find("m:" + name, _NS)
        return _omml(found, builder) if found is not None else ""

    tag = _tag(element)
    if tag in ("t", "chr"):
        text = element.text or _word_value(element, "m")
        symbols = {"α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
                   "ϵ": r"\epsilon ", "ε": r"\varepsilon ", "θ": r"\theta ", "λ": r"\lambda ",
                   "μ": r"\mu ", "π": r"\pi ", "σ": r"\sigma ", "ϕ": r"\phi ", "ω": r"\omega ",
                   "∞": r"\infty ", "∑": r"\sum ", "∫": r"\int ", "×": r"\times ", "≤": r"\le ",
                   "≥": r"\ge ", "≠": r"\ne ", "∈": r"\in ", "∼": r"\sim ", "−": "-"}
        return "".join(symbols.get(c, c) for c in text)
    if tag in ("oMath", "oMathPara", "e", "num", "den", "sup", "sub", "deg", "r", "fName", "lim"):
        return "".join(_omml(item, builder) for item in element if not _tag(item).endswith("Pr"))
    if tag.endswith("Pr"):
        return ""
    if tag == "f":
        fraction_type = _word_value(element.find("m:fPr/m:type", _NS), "m")
        if fraction_type == "noBar":
            return r"\genfrac{}{}{0pt}{}{" + child("num") + "}{" + child("den") + "}"
        if fraction_type == "lin":
            return "{" + child("num") + "}/{" + child("den") + "}"
        if fraction_type not in ("", "bar"):
            builder.warn("部分 Word 分式样式无法精确还原，已保留分子分母，请对照源文件核对。")
        return r"\frac{" + child("num") + "}{" + child("den") + "}"
    if tag == "rad":
        degree = child("deg")
        return r"\sqrt" + ("[" + degree + "]" if degree else "") + "{" + child("e") + "}"
    if tag in ("sSup", "sSub", "sSubSup"):
        return "{" + child("e") + "}" + ("_{" + child("sub") + "}" if tag != "sSup" else "") + ("^{" + child("sup") + "}" if tag != "sSub" else "")
    if tag == "acc":
        char = _word_value(element.find("m:accPr/m:chr", _NS), "m")
        command = {"̂": r"\hat", "̄": r"\bar", "⃗": r"\vec", "̃": r"\tilde", "̇": r"\dot", "̈": r"\ddot"}.get(char)
        if command:
            return command + "{" + child("e") + "}"
    if tag == "bar":
        position = _word_value(element.find("m:barPr/m:pos", _NS), "m")
        return (r"\underline{" if position == "bot" else r"\overline{") + child("e") + "}"
    if tag == "d":
        begin_node = element.find("m:dPr/m:begChr", _NS)
        end_node = element.find("m:dPr/m:endChr", _NS)
        sep_node = element.find("m:dPr/m:sepChr", _NS)
        begin = _word_value(begin_node, "m") if begin_node is not None else "("
        end = _word_value(end_node, "m") if end_node is not None else ")"
        separator = _word_value(sep_node, "m") if sep_node is not None else "|"
        begin, end = (r"\{" if begin == "{" else begin), (r"\}" if end == "}" else end)
        return begin + separator.join(_omml(part, builder) for part in element.findall("m:e", _NS)) + end
    if tag == "func":
        return child("fName") + " " + child("e")
    if tag == "nary":
        char = _word_value(element.find("m:naryPr/m:chr", _NS), "m") or "∫"
        command = {"∫": r"\int", "∑": r"\sum", "∏": r"\prod", "⋃": r"\bigcup", "⋂": r"\bigcap"}.get(char)
        if command:
            return command + ("_{" + child("sub") + "}" if child("sub") else "") + ("^{" + child("sup") + "}" if child("sup") else "") + " " + child("e")
    if tag in ("eqArr", "m"):
        rows = []
        for item in element:
            if _tag(item) in ("e", "mr"):
                rows.append(" & ".join(_omml(cell, builder) for cell in item) if _tag(item) == "mr" else _omml(item, builder))
        return (r"\begin{matrix}" if tag == "m" else r"\begin{aligned}") + r" \\ ".join(rows) + (r"\end{matrix}" if tag == "m" else r"\end{aligned}")
    builder.warn("部分 Word 数学格式仅能保留可辨识内容，请对照源文件核对复杂公式。")
    return "".join(_omml(item, builder) for item in element if not _tag(item).endswith("Pr"))


def _docx(data: bytes, builder: _Builder):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise DocumentExtractionError("无法读取 DOCX，请确认文件为未损坏的 .docx 文档；旧版 .doc 请先另存为 DOCX。") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > 4096 or sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES:
            raise DocumentExtractionError("DOCX 解压内容过大，请精简文档后导入。")
        names = set()
        for item in infos:
            # ZipInfo normalizes backslashes on Windows and truncates at NUL.
            # Inspect the original name as well as the normalized lookup key.
            original = item.orig_filename
            path = PurePosixPath(original)
            if (item.filename in names or path.is_absolute() or ".." in path.parts or "\\" in original
                    or ":" in original or "\x00" in original or item.flag_bits & 1 or item.file_size > 30 * 1024 * 1024):
                raise DocumentExtractionError("DOCX 包含不安全、加密或过大的内部文件。")
            names.add(item.filename)
        if "word/document.xml" not in names:
            raise DocumentExtractionError("DOCX 缺少正文，或上传的文件不是 Word 文档。")
        root = _xml(archive.read("word/document.xml"))
        relations = {}
        if "word/_rels/document.xml.rels" in names:
            for item in _xml(archive.read("word/_rels/document.xml.rels")):
                if item.get("TargetMode", "").lower() == "external":
                    relations[item.get("Id")] = None
                    continue
                target = unquote(item.get("Target", ""))
                # Only media within the main document's package directory.
                clean = target[1:] if target.startswith("/word/media/") else "word/" + target
                path = PurePosixPath(clean)
                relations[item.get("Id")] = clean if (".." not in path.parts and "\\" not in clean and clean.startswith("word/media/") and clean in names) else None
        styles = {}
        if "word/styles.xml" in names:
            for style in _xml(archive.read("word/styles.xml")).findall("w:style", _NS):
                name = _word_value(style.find("w:name", _NS))
                outline = _word_value(style.find("w:pPr/w:outlineLvl", _NS))
                heading = re.search(r"(?:heading|标题)\s*([1-6])", name, re.I)
                level = int(outline) + 1 if outline.isdigit() and int(outline) < 6 else (int(heading[1]) if heading else 0)
                styles[style.get("{" + _NS["w"] + "}styleId")] = level
        if "docProps/core.xml" in names:
            core = _xml(archive.read("docProps/core.xml"))
            title = core.find("{http://purl.org/dc/elements/1.1/}title")
            builder.title = (title.text or "").strip() if title is not None else ""

        def content(element) -> tuple[str, list[tuple[bytes | None, str]]]:
            tag = _tag(element)
            if tag == "del":
                return "", []
            if element.tag in ("{" + _NS["m"] + "}oMath", "{" + _NS["m"] + "}oMathPara"):
                return r"\(" + _omml(element, builder).strip() + r"\)", []
            if tag == "t" and element.tag.startswith("{" + _NS["w"] + "}"):
                return element.text or "", []
            if tag in ("br", "cr", "tab"):
                return " ", []
            if tag in ("drawing", "pict"):
                found = []
                for image in element.iter():
                    if _tag(image) == "blip":
                        rid = image.get("{" + _NS["r"] + "}embed") or image.get("{" + _NS["r"] + "}link")
                        path = relations.get(rid)
                        found.append((archive.read(path) if path else None, ""))
                    elif _tag(image) == "imagedata":
                        path = relations.get(image.get("{" + _NS["r"] + "}id"))
                        found.append((archive.read(path) if path else None, ""))
                return "", found
            pieces, images = [], []
            for item in element:
                value, media = content(item)
                pieces.append(value)
                images.extend(media)
            return "".join(pieces) + ("\n" if tag == "p" else ""), images

        body = root.find("w:body", _NS)
        if body is None:
            raise DocumentExtractionError("DOCX 缺少可读取的正文。")
        for element in body:
            if _tag(element) == "p":
                value, images = content(element)
                style = _word_value(element.find("w:pPr/w:pStyle", _NS))
                direct = _word_value(element.find("w:pPr/w:outlineLvl", _NS))
                heading = re.search(r"(?:heading|标题)\s*([1-6])", style, re.I)
                level = int(direct) + 1 if direct.isdigit() and int(direct) < 6 else styles.get(style, int(heading[1]) if heading else 0)
                builder.paragraph(value, level)
                for image, caption in images:
                    builder.image(image, caption)
            elif _tag(element) == "tbl":
                for row in element.findall("w:tr", _NS):
                    texts, images = [], []
                    for cell in row.findall("w:tc", _NS):
                        value, media = content(cell)
                        texts.append(value.strip())
                        images.extend(media)
                    builder.paragraph(" | ".join(texts))
                    for image, caption in images:
                        builder.image(image, caption)


def extract_document(data: bytes, filename: str) -> dict:
    """Return a JSON-compatible paper plus private PNG ``_assets`` for import.

    Filename chooses the parser; it is never used as a local filesystem path.
    PDF remains handled by the existing dedicated PDF import path.
    """
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix not in FORMATS:
        raise DocumentExtractionError("支持导入 PDF、DOCX、Markdown、TXT 和 HTML；旧版 DOC 请先另存为 DOCX。")
    if not data:
        raise DocumentExtractionError("文件为空，请选择包含正文的文档。")
    builder = _Builder(filename)
    kind, media = FORMATS[suffix]
    try:
        if kind == "docx":
            _docx(data, builder)
        elif kind == "html":
            reader = _HTMLReader(builder)
            reader.feed(_text(data))
            reader.close()
            reader.flush()
            builder.title = re.sub(r"\s+", " ", "".join(reader.title)).strip()
        elif kind == "markdown":
            _markdown(_text(data), builder)
        else:
            for block in re.split(r"\n\s*\n|\f", _text(data)):
                numbered = re.fullmatch(r"\s*(\d+(?:\.\d+){0,5})[.)]?\s+([^\n]{1,120})\s*", block)
                common = re.fullmatch(r"\s*(?:abstract|introduction|methods?|results?|discussion|conclusions?|references|摘要|引言|方法|结果|讨论|结论|参考文献)\s*", block, re.I)
                level = min(6, numbered[1].count(".") + 1) if numbered else (1 if common else 0)
                builder.paragraph(block, level)
        return builder.finish(kind, media)
    except DocumentExtractionError:
        raise
    except (zipfile.BadZipFile, RuntimeError, KeyError, OSError, RecursionError, UnicodeError):
        raise DocumentExtractionError("文档结构损坏或过于复杂，请重新保存后导入。") from None
