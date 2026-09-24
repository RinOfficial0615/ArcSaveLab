"""Lossless Android SharedPreferences XML adapter."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .xml_spans import (
    TextReplacement,
    XmlNode,
    XmlSpanDocument,
    XmlSpanError,
    escape_xml_attribute,
    escape_xml_text,
)

SUPPORTED_KINDS = frozenset({"string", "int", "long", "float", "boolean", "set"})


@dataclass(frozen=True, slots=True)
class Preference:
    """A decoded SharedPreferences value."""

    kind: str
    value: Any


def _format_float(value: object) -> str:
    number = float(str(value))
    return format(number, ".17g")


def _infer_kind(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (set, frozenset, list, tuple)):
        return "set"
    return "string"


def _coerce_value(kind: str, value: object) -> object:
    if kind == "string":
        return str(value)
    if kind in {"int", "long"}:
        if isinstance(value, bool):
            raise TypeError(f"boolean is not a valid {kind}")
        return int(str(value), 10)
    if kind == "float":
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid float")
        return float(str(value))
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise TypeError("boolean preference requires bool or 'true'/'false'")
    if kind == "set":
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
            raise TypeError("set preference requires an iterable of strings")
        return tuple(str(item) for item in value)
    raise ValueError(f"unsupported SharedPreferences kind: {kind}")


@dataclass(slots=True)
class SharedPrefsDocument:
    """Typed view over a byte-preserving SharedPreferences document.

    Duplicate names follow Android's effective last-wins behavior.  Updating a
    duplicate changes only the effective (last) element; deleting a name removes
    every occurrence so an older value cannot become visible again.
    """

    _xml: XmlSpanDocument
    source_path: Path | None = None
    _entries: dict[str, Preference] = field(init=False, repr=False)
    _nodes: dict[str, XmlNode] = field(init=False, repr=False)
    _all_nodes: dict[str, list[XmlNode]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        source_path: str | os.PathLike[str] | None = None,
    ) -> SharedPrefsDocument:
        return cls(
            XmlSpanDocument.from_bytes(data),
            Path(source_path) if source_path is not None else None,
        )

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> SharedPrefsDocument:
        source = Path(path)
        return cls(XmlSpanDocument.from_path(source), source)

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> SharedPrefsDocument:
        return cls.from_path(path)

    def _reindex(self) -> None:
        root = self._xml.root
        if root.name != "map":
            raise XmlSpanError("expected Android SharedPreferences <map> root")
        entries: dict[str, Preference] = {}
        nodes: dict[str, XmlNode] = {}
        all_nodes: dict[str, list[XmlNode]] = {}
        for node in root.children:
            name_attribute = node.attribute("name")
            if name_attribute is None:
                continue
            name = name_attribute.value
            try:
                preference = self._decode(node)
            except ValueError as exc:
                raise XmlSpanError(f"invalid preference {name!r}: {exc}") from exc
            entries[name] = preference
            nodes[name] = node
            all_nodes.setdefault(name, []).append(node)
        self._entries = entries
        self._nodes = nodes
        self._all_nodes = all_nodes

    def _decode(self, node: XmlNode) -> Preference:
        kind = node.name
        if kind == "string":
            value: object = self._xml.text_content(node)
        elif kind in {"int", "long"}:
            attribute = node.attribute("value")
            if attribute is None:
                raise ValueError(f"<{kind}> has no value attribute")
            value = int(attribute.value)
        elif kind == "float":
            attribute = node.attribute("value")
            if attribute is None:
                raise ValueError("<float> has no value attribute")
            value = float(attribute.value)
        elif kind == "boolean":
            attribute = node.attribute("value")
            if attribute is None or attribute.value.lower() not in {"true", "false"}:
                raise ValueError("<boolean> requires value='true' or value='false'")
            value = attribute.value.lower() == "true"
        elif kind == "set":
            value = tuple(
                self._xml.text_content(child) for child in node.children if child.name == "string"
            )
        else:
            # Unknown named elements remain visible to expert callers and, more
            # importantly, are never touched by edits to other preferences.
            value = self._xml.inner_xml(node)
        return Preference(kind=kind, value=value)

    @property
    def entries(self) -> Mapping[str, Preference]:
        return MappingProxyType(self._entries)

    def as_dict(self) -> dict[str, Preference]:
        return dict(self._entries)

    def items(self) -> Iterator[tuple[str, Preference]]:
        return iter(self._entries.items())

    def keys(self) -> Iterator[str]:
        return iter(self._entries)

    def __iter__(self) -> Iterator[str]:
        return self.keys()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def get_entry(self, name: str) -> Preference | None:
        return self._entries.get(name)

    def get(self, name: str, default: Any = None) -> Any:
        preference = self._entries.get(name)
        return default if preference is None else preference.value

    def kind(self, name: str) -> str | None:
        preference = self._entries.get(name)
        return preference.kind if preference else None

    def __getitem__(self, name: str) -> Any:
        return self._entries[name].value

    def _adopt(self, xml: XmlSpanDocument) -> None:
        if xml is self._xml:
            return
        self._xml = xml
        self._reindex()

    def _render_entry(self, name: str, value: object, kind: str) -> str:
        quote = '"'
        escaped_name = escape_xml_attribute(name, quote)
        coerced = _coerce_value(kind, value)
        if kind == "string":
            return f"<string name={quote}{escaped_name}{quote}>{escape_xml_text(coerced)}</string>"
        if kind in {"int", "long"}:
            return f"<{kind} name={quote}{escaped_name}{quote} value={quote}{coerced}{quote} />"
        if kind == "float":
            rendered = _format_float(coerced)
            return f"<float name={quote}{escaped_name}{quote} value={quote}{rendered}{quote} />"
        if kind == "boolean":
            rendered = "true" if coerced else "false"
            return f"<boolean name={quote}{escaped_name}{quote} value={quote}{rendered}{quote} />"
        if kind == "set":
            values = cast(tuple[str, ...], coerced)
            if not values:
                return f"<set name={quote}{escaped_name}{quote}></set>"
            newline = self._xml.newline()
            children = "".join(
                f"{newline}        <string>{escape_xml_text(item)}</string>" for item in values
            )
            return f"<set name={quote}{escaped_name}{quote}>{children}{newline}    </set>"
        raise ValueError(f"unsupported SharedPreferences kind: {kind}")

    def _set_same_kind(self, node: XmlNode, preference: Preference, value: object) -> None:
        kind = preference.kind
        coerced = _coerce_value(kind, value)
        if preference.value == coerced:
            return
        if kind == "string":
            self._adopt(self._xml.replace_element_text(node, coerced))
            return
        if kind in {"int", "long"}:
            self._adopt(self._xml.replace_attribute(node, "value", str(coerced)))
            return
        if kind == "float":
            self._adopt(self._xml.replace_attribute(node, "value", _format_float(coerced)))
            return
        if kind == "boolean":
            self._adopt(self._xml.replace_attribute(node, "value", "true" if coerced else "false"))
            return
        if kind == "set":
            values = cast(tuple[str, ...], coerced)
            strings = [child for child in node.children if child.name == "string"]
            if len(strings) == len(values):
                replacements = [
                    TextReplacement(
                        child.inner_start,
                        child.inner_end,
                        escape_xml_text(item),
                    )
                    for child, item in zip(strings, values, strict=True)
                ]
                self._adopt(self._xml.replace(replacements))
                return
            newline = self._xml.newline()
            indent = self._xml.line_indent(node.start) + "    "
            body = "".join(
                f"{newline}{indent}<string>{escape_xml_text(item)}</string>" for item in values
            )
            if values:
                body += newline + self._xml.line_indent(node.start)
            self._adopt(self._xml.replace_span(node.inner_start, node.inner_end, body))
            return
        raise ValueError(f"cannot update unknown preference kind <{kind}>")

    def set(self, name: str, value: object, *, kind: str | None = None) -> None:
        """Set a preference, preserving an existing element's type by default."""

        existing = self._entries.get(name)
        selected_kind = kind or (existing.kind if existing else _infer_kind(value))
        if selected_kind not in SUPPORTED_KINDS:
            raise ValueError(f"unsupported SharedPreferences kind: {selected_kind}")
        coerced = _coerce_value(selected_kind, value)
        if existing is not None:
            node = self._nodes[name]
            if existing.kind == selected_kind:
                self._set_same_kind(node, existing, coerced)
            else:
                markup = self._render_entry(name, coerced, selected_kind)
                self._adopt(self._xml.replace_span(node.start, node.end, markup))
            return
        markup = self._render_entry(name, coerced, selected_kind)
        self._adopt(self._xml.append_child(self._xml.root, markup))

    def update(
        self,
        values: Mapping[str, object] | Iterable[tuple[str, object]],
    ) -> None:
        pairs = values.items() if isinstance(values, Mapping) else values
        for name, value in pairs:
            self.set(name, value)

    def delete(self, name: str) -> bool:
        nodes = self._all_nodes.get(name)
        if not nodes:
            return False
        self._adopt(
            self._xml.replace([TextReplacement(node.start, node.end, "") for node in nodes])
        )
        return True

    def to_bytes(self) -> bytes:
        return self._xml.to_bytes()

    def write(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.source_path
        if destination is None:
            raise ValueError("write path is required for an in-memory document")
        destination.write_bytes(self.to_bytes())
        self.source_path = destination
        return destination


def parse_shared_prefs(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview | SharedPrefsDocument,
) -> SharedPrefsDocument:
    """Coerce a path, byte buffer or existing document to a typed document."""

    if isinstance(source, SharedPrefsDocument):
        return source
    if isinstance(source, (bytes, bytearray, memoryview)):
        return SharedPrefsDocument.from_bytes(source)
    return SharedPrefsDocument.from_path(source)


__all__ = [
    "Preference",
    "SUPPORTED_KINDS",
    "SharedPrefsDocument",
    "parse_shared_prefs",
]
