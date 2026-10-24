from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import requests

from ..utils import UserError, short_error


def get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    what: str = "данные",
) -> Any:
    try:
        response = session.get(url, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise UserError(
            f"Не удалось получить {what}: {short_error(exc)}",
            hint="Проверьте интернет, VPN/прокси или попробуйте позже.",
        ) from exc

    if response.status_code == 404:
        raise UserError(f"{what.capitalize()} не найдены. Возможно, ссылка неверная или контент удалён.")
    if response.status_code in (401, 403):
        raise UserError(
            f"Сервис отклонил запрос ({response.status_code}) при получении: {what}.",
            hint="Возможно, нужен VPN/прокси или авторизация. Проверьте настройки.",
        )
    if response.status_code == 429:
        raise UserError(
            "Сервис ограничил частоту запросов (429).",
            hint="Подождите несколько минут и попробуйте снова.",
        )
    if response.status_code == 451:
        raise UserError(
            f"Сервис недоступен из текущей сети (451) при получении: {what}.",
            hint="Включите VPN/прокси в настройках или смените сеть.",
        )
    if response.status_code >= 400:
        raise UserError(
            f"Сервис вернул ошибку {response.status_code} при получении: {what}.",
            hint="Попробуйте позже или проверьте ссылку.",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise UserError(
            f"Сервис вернул не JSON при получении: {what}.",
            hint="Возможно, изменился API или запрос заблокирован.",
        ) from exc


def absolute_url(value: str | None, scheme: str = "https://") -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return scheme + value


def fix_yandex_cover(uri: str | None, size: str = "1000x1000") -> str | None:
    url = absolute_url(uri)
    if not url:
        return None
    return url.replace("%%", size)


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def platform_from_host(host: str) -> str:
    host = (host or "").lower().removeprefix("www.").removeprefix("m.")
    if host.endswith("soundcloud.com") or host == "on.soundcloud.com":
        return "soundcloud"
    if host.startswith("music.yandex.") or host.startswith("y.music.yandex."):
        return "yandex"
    if host in {"youtube.com", "youtu.be", "music.youtube.com"}:
        return "youtube"
    if host.endswith("bandcamp.com"):
        return "bandcamp"
    return "generic"
