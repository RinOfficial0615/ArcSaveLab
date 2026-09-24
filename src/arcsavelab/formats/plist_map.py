"""Lossless Cocos/Apple plist ValueMap adapter."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .xml_spans import (
    TextReplacement,
    XmlNode,
    XmlSpanDocument,
    XmlSpanError,
    escape_xml_text,
)

SCALAR_KINDS = frozenset({"string", "integer", "real", "true", "false"})


@dataclass(frozen=True, slots=True)
class PlistValue:
    kind: str
    value: Any

    def as_string(self) -> str:
        """Replicate the scalar subset of ``cocos2d::Value::asString``."""

        if self.kind == "integer":
            return str(int(self.value))
        if self.kind == "real":
            return format(float(self.value), ".16g")
        if self.kind == "string":
            return str(self.value)
        if self.kind == "true":
            return "true"
        if self.kind == "false":
            return "false"
        raise ValueError(f"unsupported ValueMap value type: <{self.kind}>")


@dataclass(frozen=True, slots=True)
class _PlistPair:
    key: str
    key_node: XmlNode
    value_node: XmlNode
    value: PlistValue


def _infer_kind(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    return "string"


def _coerce(kind: str, value: object) -> str | int | float | bool:
    if kind == "string":
        return str(value)
    if kind == "integer":
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid plist integer")
        return int(str(value), 10)
    if kind == "real":
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid plist real")
        return float(str(value))
    if kind in {"true", "false"}:
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise TypeError("plist boolean requires a bool")
        return bool(value)
    raise ValueError(f"unsupported editable plist kind: <{kind}>")


@dataclass(slots=True)
class PlistMapDocument:
    """Top-level plist dictionary with lossless last-wins updates."""

    _xml: XmlSpanDocument
    source_path: Path | None = None
    _dict_node: XmlNode = field(init=False, repr=False)
    _entries: dict[str, PlistValue] = field(init=False, repr=False)
    _pairs: dict[str, _PlistPair] = field(init=False, repr=False)
    _all_pairs: dict[str, list[_PlistPair]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        source_path: str | os.PathLike[str] | None = None,
    ) -> PlistMapDocument:
        return cls(
            XmlSpanDocument.from_bytes(data),
            Path(source_path) if source_path is not None else None,
        )

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> PlistMapDocument:
        source = Path(path)
        return cls(XmlSpanDocument.from_path(source), source)

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> PlistMapDocument:
        return cls.from_path(path)

    def _find_dict(self) -> XmlNode:
        root = self._xml.root
        if root.name == "dict":
            return root
        if root.name == "plist":
            for child in root.children:
                if child.name == "dict":
                    return child
        raise XmlSpanError("plist has no top-level <dict>")

    def _decode_value(self, node: XmlNode) -> PlistValue:
        kind = node.name
        text = self._xml.text_content(node)
        if kind == "integer":
            value: object = int(text.strip())
        elif kind == "real":
            value = float(text.strip())
        elif kind == "string":
            value = text
        elif kind == "true":
            value = True
        elif kind == "false":
            value = False
        else:
            # Unknown plist types stay queryable as raw inner XML.  They are not
            # accepted by ValueMap hashing, matching the native scalar contract.
            value = self._xml.inner_xml(node)
        return PlistValue(kind=kind, value=value)

    def _reindex(self) -> None:
        dictionary = self._find_dict()
        children = dictionary.children
        entries: dict[str, PlistValue] = {}
        pairs: dict[str, _PlistPair] = {}
        all_pairs: dict[str, list[_PlistPair]] = {}
        index = 0
        while index < len(children):
            key_node = children[index]
            if key_node.name != "key" or index + 1 >= len(children):
                raise XmlSpanError(
                    f"malformed plist dictionary at child {index}: expected <key>/value pair"
                )
            value_node = children[index + 1]
            key = self._xml.text_content(key_node)
            value = self._decode_value(value_node)
            pair = _PlistPair(key, key_node, value_node, value)
            entries[key] = value
            pairs[key] = pair
            all_pairs.setdefault(key, []).append(pair)
            index += 2
        self._dict_node = dictionary
        self._entries = entries
        self._pairs = pairs
        self._all_pairs = all_pairs

    @property
    def entries(self) -> Mapping[str, PlistValue]:
        return MappingProxyType(self._entries)

    def as_dict(self) -> dict[str, PlistValue]:
        return dict(self._entries)

    def items(self) -> Iterator[tuple[str, PlistValue]]:
        return iter(self._entries.items())

    def keys(self) -> Iterator[str]:
        return iter(self._entries)

    def __iter__(self) -> Iterator[str]:
        return self.keys()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def get_entry(self, key: str) -> PlistValue | None:
        return self._entries.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._entries.get(key)
        return default if value is None else value.value

    def kind(self, key: str) -> str | None:
        value = self._entries.get(key)
        return value.kind if value else None

    def __getitem__(self, key: str) -> Any:
        return self._entries[key].value

    def _adopt(self, xml: XmlSpanDocument) -> None:
        if xml is self._xml:
            return
        self._xml = xml
        self._reindex()

    def _render_value(self, value: object, kind: str) -> str:
        coerced = _coerce(kind, value)
        if kind == "string":
            return f"<string>{escape_xml_text(coerced)}</string>"
        if kind == "integer":
            return f"<integer>{coerced}</integer>"
        if kind == "real":
            return f"<real>{format(float(coerced), '.16g')}</real>"
        return "<true/>" if bool(coerced) else "<false/>"

    def _set_same_kind(self, pair: _PlistPair, value: object) -> None:
        kind = pair.value.kind
        coerced = _coerce(kind, value)
        if kind in {"true", "false"}:
            desired_kind = "true" if bool(coerced) else "false"
            if desired_kind == kind:
                return
            self._adopt(
                self._xml.replace_span(
                    pair.value_node.start,
                    pair.value_node.end,
                    "<true/>" if desired_kind == "true" else "<false/>",
                )
            )
            return
        if pair.value.value == coerced:
            return
        rendered = format(float(coerced), ".16g") if kind == "real" else str(coerced)
        self._adopt(self._xml.replace_element_text(pair.value_node, rendered))

    def set(self, key: str, value: object, *, kind: str | None = None) -> None:
        """Set a value; existing scalar types are preserved unless overridden."""

        existing = self._entries.get(key)
        selected_kind = kind or (existing.kind if existing else _infer_kind(value))
        if selected_kind not in SCALAR_KINDS:
            raise ValueError(f"unsupported editable plist kind: <{selected_kind}>")

        # Explicit bool kind is treated as the requested literal state.  For an
        # existing boolean, passing True/False naturally selects true/false.
        if selected_kind in {"true", "false"} and isinstance(value, bool):
            selected_kind = "true" if value else "false"

        if existing is not None:
            pair = self._pairs[key]
            if existing.kind == selected_kind or (
                existing.kind in {"true", "false"} and selected_kind in {"true", "false"}
            ):
                self._set_same_kind(pair, value)
            else:
                markup = self._render_value(value, selected_kind)
                self._adopt(
                    self._xml.replace_span(pair.value_node.start, pair.value_node.end, markup)
                )
            return

        key_markup = f"<key>{escape_xml_text(key)}</key>"
        value_markup = self._render_value(value, selected_kind)
        newline = self._xml.newline()
        if self._dict_node.children:
            indent = self._xml.line_indent(self._dict_node.children[-1].start)
            position = self._dict_node.children[-1].end
        else:
            indent = self._xml.line_indent(self._dict_node.start) + "    "
            position = self._dict_node.start_tag_end
        markup = newline + indent + key_markup + newline + indent + value_markup
        self._adopt(self._xml.replace_span(position, position, markup))

    def update(self, values: Mapping[str, object] | Iterable[tuple[str, object]]) -> None:
        pairs = values.items() if isinstance(values, Mapping) else values
        for key, value in pairs:
            self.set(key, value)

    def delete(self, key: str) -> bool:
        pairs = self._all_pairs.get(key)
        if not pairs:
            return False
        self._adopt(
            self._xml.replace(
                [TextReplacement(pair.key_node.start, pair.value_node.end, "") for pair in pairs]
            )
        )
        return True

    def serialize_value_map(self) -> str:
        keys = sorted(self._entries, key=lambda item: item.encode("utf-8"), reverse=True)
        return "".join(f"{key}&{self._entries[key].as_string()}$" for key in keys)

    def to_bytes(self) -> bytes:
        return self._xml.to_bytes()

    def write(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.source_path
        if destination is None:
            raise ValueError("write path is required for an in-memory document")
        destination.write_bytes(self.to_bytes())
        self.source_path = destination
        return destination


def parse_plist_map(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview | PlistMapDocument,
) -> PlistMapDocument:
    if isinstance(source, PlistMapDocument):
        return source
    if isinstance(source, (bytes, bytearray, memoryview)):
        return PlistMapDocument.from_bytes(source)
    return PlistMapDocument.from_path(source)


__all__ = [
    "PlistMapDocument",
    "PlistValue",
    "SCALAR_KINDS",
    "parse_plist_map",
]
