from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "developer"]
    content: Any


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = None

    # Accept extra fields from clients (temperature, etc.).
    model_config = ConfigDict(extra="allow")


class ErrorResponse(BaseModel):
    error: dict[str, Any] = Field(default_factory=dict)


def normalize_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    if isinstance(content, dict):
        if content.get("type") == "text" and isinstance(content.get("text"), str):
            return content["text"]
    return str(content)


def messages_to_prompt(messages: list[ChatMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.role.upper()
        text = normalize_message_content(message.content)
        parts.append(f"{role}: {text}")
    return "\n\n".join(parts).strip()


def extract_image_urls_from_content(content: Any) -> list[str]:
    urls: list[str] = []
    if content is None:
        return urls

    # Accept single-part formats in addition to the OpenAI list-of-parts format.
    if isinstance(content, dict):
        part_type = content.get("type")
        if part_type in {"image_url", "input_image"}:
            image = content.get("image_url")
            if isinstance(image, dict):
                url = image.get("url")
                if isinstance(url, str) and url:
                    urls.append(url)
            elif isinstance(image, str) and image:
                urls.append(image)
        return urls

    if not isinstance(content, list):
        return urls

    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type not in {"image_url", "input_image"}:
            continue
        image = part.get("image_url")
        if isinstance(image, dict):
            url = image.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
        elif isinstance(image, str) and image:
            urls.append(image)

    return urls


def extract_image_urls(messages: list[ChatMessage]) -> list[str]:
    urls: list[str] = []
    for message in messages:
        urls.extend(extract_image_urls_from_content(message.content))
    return urls
