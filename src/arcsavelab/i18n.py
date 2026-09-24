from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from typing import Any

SUPPORTED_LOCALES = ("en", "zh-Hans")


class Translator:
    def __init__(self, locale: str = "en") -> None:
        self._locale = locale if locale in SUPPORTED_LOCALES else "en"
        self._catalogs: dict[str, Mapping[str, str]] = {}

    @property
    def locale(self) -> str:
        return self._locale

    def switch(self, locale: str) -> None:
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported locale: {locale}")
        self._locale = locale

    def _catalog(self, locale: str) -> Mapping[str, str]:
        if locale not in self._catalogs:
            resource = files("arcsavelab.resources.i18n").joinpath(f"{locale}.json")
            self._catalogs[locale] = json.loads(resource.read_text(encoding="utf-8"))
        return self._catalogs[locale]

    def gettext(self, message_id: str, **arguments: Any) -> str:
        catalog = self._catalog(self._locale)
        fallback = self._catalog("en")
        template = catalog.get(message_id, fallback.get(message_id, message_id))
        try:
            return template.format(**arguments)
        except (KeyError, ValueError):
            return template

    __call__ = gettext
