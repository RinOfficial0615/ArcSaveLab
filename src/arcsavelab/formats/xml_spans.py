"""Lossless XML token/span support.

This module deliberately does not build and re-serialize an XML tree.  It
indexes element and attribute spans in the original character stream so an
editor can replace only the token that changed.  Declarations, doctypes,
comments, processing instructions, whitespace and unknown elements therefore
remain byte-for-byte intact.

The parser is intentionally small, but it understands the XML constructs used
by Android SharedPreferences and Apple/Cocos plist files, including quoted
attributes, comments, CDATA and doctypes with an internal subset.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


class XmlSpanError(ValueError):
    """Raised when XML cannot be indexed without losing span information."""


_ENCODING_RE = re.compile(rb"<\?xml\s+[^>]*encoding\s*=\s*(['\"])([^'\"]+)\1", re.IGNORECASE)
_NAME_END = frozenset(" \t\r\n/=><")
_ENTITY_RE = re.compile(r"&(#x[0-9A-Fa-f]+|#[0-9]+|lt|gt|amp|quot|apos);")


def local_name(qname: str) -> str:
    """Return the local part of an XML qualified name."""

    return qname.rsplit(":", 1)[-1]


def xml_unescape(value: str) -> str:
    """Decode XML predefined and numeric character references.

    Unknown named entities are kept verbatim.  This is important for lossless
    documents containing a custom doctype: the span editor must not guess at
    the entity declaration's replacement text.
    """

    predefined = {
        "lt": "<",
        "gt": ">",
        "amp": "&",
        "quot": '"',
        "apos": "'",
    }

    def replace(match: re.Match[str]) -> str:
        entity = match.group(1)
        if entity.startswith("#x"):
            try:
                return chr(int(entity[2:], 16))
            except (ValueError, OverflowError):
                return match.group(0)
        if entity.startswith("#"):
            try:
                return chr(int(entity[1:], 10))
            except (ValueError, OverflowError):
                return match.group(0)
        return predefined[entity]

    return _ENTITY_RE.sub(replace, value)


def escape_xml_text(value: object) -> str:
    """Escape a value for XML character data."""

    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_xml_attribute(value: object, quote: str = '"') -> str:
    """Escape a value for a quoted XML attribute."""

    escaped = escape_xml_text(value)
    if quote == "'":
        return escaped.replace("'", "&apos;")
    return escaped.replace('"', "&quot;")


@dataclass(frozen=True, slots=True)
class XmlAttribute:
    """An attribute and the exact spans of its name and decoded value."""

    qname: str
    value: str
    name_start: int
    name_end: int
    value_start: int
    value_end: int
    quote: str

    @property
    def name(self) -> str:
        return local_name(self.qname)


@dataclass(slots=True)
class XmlNode:
    """An element indexed into :class:`XmlSpanDocument.text`."""

    qname: str
    start: int
    name_start: int
    name_end: int
    start_tag_end: int
    end_tag_start: int
    end: int
    self_closing: bool
    attributes: tuple[XmlAttribute, ...] = ()
    children: list[XmlNode] = field(default_factory=list)
    parent: XmlNode | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return local_name(self.qname)

    @property
    def inner_start(self) -> int:
        return self.start_tag_end

    @property
    def inner_end(self) -> int:
        return self.end_tag_start

    def attribute(self, name: str) -> XmlAttribute | None:
        """Return the last matching attribute.

        XML itself rejects duplicate attributes, but last-wins keeps this
        accessor deterministic for damaged real-world files.
        """

        found: XmlAttribute | None = None
        for attribute in self.attributes:
            if attribute.qname == name or attribute.name == name:
                found = attribute
        return found


@dataclass(frozen=True, slots=True)
class TextReplacement:
    start: int
    end: int
    text: str


def _detect_encoding(data: bytes) -> tuple[str, bytes, bytes]:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8", b"\xef\xbb\xbf", data[3:]
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le", b"\xff\xfe", data[2:]
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be", b"\xfe\xff", data[2:]
    match = _ENCODING_RE.search(data[:512])
    encoding = match.group(2).decode("ascii") if match else "utf-8"
    return encoding, b"", data


def _find_tag_end(text: str, start: int) -> int:
    quote: str | None = None
    index = start + 1
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == ">":
            return index + 1
        index += 1
    raise XmlSpanError(f"unterminated tag at character {start}")


def _find_doctype_end(text: str, start: int) -> int:
    quote: str | None = None
    subset_depth = 0
    index = start + 2
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "[":
            subset_depth += 1
        elif char == "]" and subset_depth:
            subset_depth -= 1
        elif char == ">" and subset_depth == 0:
            return index + 1
        index += 1
    raise XmlSpanError(f"unterminated declaration at character {start}")


def _parse_start_tag(
    text: str, start: int, end: int
) -> tuple[str, int, int, bool, tuple[XmlAttribute, ...]]:
    index = start + 1
    while index < end and text[index].isspace():
        index += 1
    name_start = index
    while index < end and text[index] not in _NAME_END:
        index += 1
    if name_start == index:
        raise XmlSpanError(f"missing element name at character {start}")
    qname = text[name_start:index]

    close = end - 2
    while close >= index and text[close].isspace():
        close -= 1
    self_closing = close >= index and text[close] == "/"
    attribute_limit = close if self_closing else close + 1

    attributes: list[XmlAttribute] = []
    while index < attribute_limit:
        while index < attribute_limit and text[index].isspace():
            index += 1
        if index >= attribute_limit:
            break
        attr_name_start = index
        while index < attribute_limit and text[index] not in _NAME_END:
            index += 1
        attr_name_end = index
        if attr_name_start == attr_name_end:
            raise XmlSpanError(f"malformed attribute at character {index}")
        while index < attribute_limit and text[index].isspace():
            index += 1
        if index >= attribute_limit or text[index] != "=":
            raise XmlSpanError(f"attribute without '=' at character {attr_name_start}")
        index += 1
        while index < attribute_limit and text[index].isspace():
            index += 1
        if index >= attribute_limit or text[index] not in {'"', "'"}:
            raise XmlSpanError(f"unquoted attribute at character {attr_name_start}")
        quote = text[index]
        value_start = index + 1
        value_end = text.find(quote, value_start, attribute_limit)
        if value_end < 0:
            raise XmlSpanError(f"unterminated attribute at character {attr_name_start}")
        attributes.append(
            XmlAttribute(
                qname=text[attr_name_start:attr_name_end],
                value=xml_unescape(text[value_start:value_end]),
                name_start=attr_name_start,
                name_end=attr_name_end,
                value_start=value_start,
                value_end=value_end,
                quote=quote,
            )
        )
        index = value_end + 1
    return qname, name_start, name_start + len(qname), self_closing, tuple(attributes)


def _parse_nodes(text: str) -> list[XmlNode]:
    roots: list[XmlNode] = []
    stack: list[XmlNode] = []
    position = 0
    while True:
        start = text.find("<", position)
        if start < 0:
            break
        if text.startswith("<!--", start):
            finish = text.find("-->", start + 4)
            if finish < 0:
                raise XmlSpanError(f"unterminated comment at character {start}")
            position = finish + 3
            continue
        if text.startswith("<![CDATA[", start):
            finish = text.find("]]>", start + 9)
            if finish < 0:
                raise XmlSpanError(f"unterminated CDATA at character {start}")
            position = finish + 3
            continue
        if text.startswith("<?", start):
            finish = text.find("?>", start + 2)
            if finish < 0:
                raise XmlSpanError(f"unterminated processing instruction at character {start}")
            position = finish + 2
            continue
        if text.startswith("<!", start):
            position = _find_doctype_end(text, start)
            continue
        if text.startswith("</", start):
            finish = _find_tag_end(text, start)
            index = start + 2
            while index < finish and text[index].isspace():
                index += 1
            name_start = index
            while index < finish and text[index] not in " \t\r\n>":
                index += 1
            qname = text[name_start:index]
            if not stack:
                raise XmlSpanError(f"unexpected closing tag </{qname}> at character {start}")
            node = stack.pop()
            if node.qname != qname:
                raise XmlSpanError(
                    f"mismatched closing tag </{qname}> for <{node.qname}> at character {start}"
                )
            node.end_tag_start = start
            node.end = finish
            position = finish
            continue

        finish = _find_tag_end(text, start)
        qname, name_start, name_end, self_closing, attributes = _parse_start_tag(
            text, start, finish
        )
        node = XmlNode(
            qname=qname,
            start=start,
            name_start=name_start,
            name_end=name_end,
            start_tag_end=finish,
            end_tag_start=finish,
            end=finish,
            self_closing=self_closing,
            attributes=attributes,
        )
        if stack:
            node.parent = stack[-1]
            stack[-1].children.append(node)
        else:
            roots.append(node)
        if not self_closing:
            stack.append(node)
        position = finish

    if stack:
        node = stack[-1]
        raise XmlSpanError(f"unterminated element <{node.qname}> at character {node.start}")
    if not roots:
        raise XmlSpanError("document has no root element")
    return roots


def _decode_text_fragment(fragment: str) -> str:
    """Return character data while ignoring comments and processing instructions."""

    result: list[str] = []
    position = 0
    while position < len(fragment):
        markup = fragment.find("<", position)
        if markup < 0:
            result.append(xml_unescape(fragment[position:]))
            break
        result.append(xml_unescape(fragment[position:markup]))
        if fragment.startswith("<![CDATA[", markup):
            finish = fragment.find("]]>", markup + 9)
            if finish < 0:
                raise XmlSpanError("unterminated CDATA in text content")
            result.append(fragment[markup + 9 : finish])
            position = finish + 3
        elif fragment.startswith("<!--", markup):
            finish = fragment.find("-->", markup + 4)
            if finish < 0:
                raise XmlSpanError("unterminated comment in text content")
            position = finish + 3
        elif fragment.startswith("<?", markup):
            finish = fragment.find("?>", markup + 2)
            if finish < 0:
                raise XmlSpanError("unterminated processing instruction in text content")
            position = finish + 2
        else:
            finish = _find_tag_end(fragment, markup)
            position = finish
    return "".join(result)


class XmlSpanDocument:
    """An immutable snapshot of XML text plus replaceable token spans."""

    def __init__(
        self,
        text: str,
        *,
        encoding: str = "utf-8",
        bom: bytes = b"",
        original_bytes: bytes | None = None,
    ) -> None:
        self.text = text
        self.encoding = encoding
        self.bom = bom
        self.roots = _parse_nodes(text)
        self._bytes = original_bytes if original_bytes is not None else bom + text.encode(encoding)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> XmlSpanDocument:
        raw = bytes(data)
        encoding, bom, payload = _detect_encoding(raw)
        try:
            text = payload.decode(encoding)
        except (LookupError, UnicodeDecodeError) as exc:
            raise XmlSpanError(f"cannot decode XML as {encoding}: {exc}") from exc
        return cls(text, encoding=encoding, bom=bom, original_bytes=raw)

    @classmethod
    def from_path(cls, path: str | Path) -> XmlSpanDocument:
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def root(self) -> XmlNode:
        if len(self.roots) != 1:
            raise XmlSpanError(f"expected one root element, found {len(self.roots)}")
        return self.roots[0]

    def to_bytes(self) -> bytes:
        return self._bytes

    def outer_xml(self, node: XmlNode) -> str:
        return self.text[node.start : node.end]

    def inner_xml(self, node: XmlNode) -> str:
        if node.self_closing:
            return ""
        return self.text[node.inner_start : node.inner_end]

    def text_content(self, node: XmlNode) -> str:
        if node.self_closing:
            return ""
        if not node.children:
            return _decode_text_fragment(self.inner_xml(node))
        pieces: list[str] = []
        position = node.inner_start
        for child in node.children:
            pieces.append(_decode_text_fragment(self.text[position : child.start]))
            pieces.append(self.text_content(child))
            position = child.end
        pieces.append(_decode_text_fragment(self.text[position : node.inner_end]))
        return "".join(pieces)

    def replace(self, replacements: Sequence[TextReplacement]) -> XmlSpanDocument:
        """Apply non-overlapping replacements and return a re-indexed document."""

        effective = [
            replacement
            for replacement in replacements
            if self.text[replacement.start : replacement.end] != replacement.text
        ]
        if not effective:
            return self
        ordered = sorted(effective, key=lambda item: (item.start, item.end))
        previous_end = -1
        for replacement in ordered:
            if replacement.start < 0 or replacement.end < replacement.start:
                raise ValueError(f"invalid replacement span {replacement}")
            if replacement.end > len(self.text):
                raise ValueError(f"replacement extends past document: {replacement}")
            if replacement.start < previous_end:
                raise ValueError("overlapping XML replacements")
            previous_end = replacement.end

        text = self.text
        for replacement in reversed(ordered):
            text = text[: replacement.start] + replacement.text + text[replacement.end :]
        raw = self.bom + text.encode(self.encoding)
        return XmlSpanDocument(
            text,
            encoding=self.encoding,
            bom=self.bom,
            original_bytes=raw,
        )

    def replace_span(self, start: int, end: int, value: str) -> XmlSpanDocument:
        return self.replace((TextReplacement(start, end, value),))

    def replace_attribute(self, node: XmlNode, name: str, value: object) -> XmlSpanDocument:
        attribute = node.attribute(name)
        if attribute is not None:
            escaped = escape_xml_attribute(value, attribute.quote)
            return self.replace_span(attribute.value_start, attribute.value_end, escaped)

        quote = node.attributes[0].quote if node.attributes else '"'
        escaped = escape_xml_attribute(value, quote)
        index = node.start_tag_end - 1
        while index > node.name_end and self.text[index - 1].isspace():
            index -= 1
        if index > node.name_end and self.text[index - 1] == "/":
            index -= 1
        return self.replace_span(index, index, f" {name}={quote}{escaped}{quote}")

    def replace_element_text(self, node: XmlNode, value: object) -> XmlSpanDocument:
        escaped = escape_xml_text(value)
        if node.self_closing:
            close = node.start_tag_end - 2
            while close > node.name_end and self.text[close].isspace():
                close -= 1
            if self.text[close] != "/":
                raise XmlSpanError(f"cannot expand self-closing <{node.qname}>")
            return self.replace_span(close, close + 2, f">{escaped}</{node.qname}>")
        return self.replace_span(node.inner_start, node.inner_end, escaped)

    def newline(self) -> str:
        return "\r\n" if "\r\n" in self.text else "\n"

    def line_indent(self, position: int) -> str:
        line_start = max(self.text.rfind("\n", 0, position), self.text.rfind("\r", 0, position))
        prefix = self.text[line_start + 1 : position]
        return prefix if prefix.isspace() or prefix == "" else ""

    def append_child(self, parent: XmlNode, markup: str) -> XmlSpanDocument:
        """Append one element while retaining the parent's existing tail."""

        if parent.self_closing:
            raise XmlSpanError(f"cannot append to self-closing <{parent.qname}>")
        newline = self.newline()
        if parent.children:
            anchor = parent.children[-1]
            indent = self.line_indent(anchor.start)
            position = anchor.end
        else:
            indent = self.line_indent(parent.start) + "    "
            position = parent.start_tag_end
        return self.replace_span(position, position, newline + indent + markup)


__all__ = [
    "TextReplacement",
    "XmlAttribute",
    "XmlNode",
    "XmlSpanDocument",
    "XmlSpanError",
    "escape_xml_attribute",
    "escape_xml_text",
    "local_name",
    "xml_unescape",
]
