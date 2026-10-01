import asyncio
import json
from json import JSONDecodeError
from typing import Any

import httpx


TRANSIENT_MODEL_STATUSES = {429, 502, 503, 504}
MODEL_RETRY_ATTEMPTS = 4
MODEL_EMPTY_OUTPUT_RETRY_ATTEMPTS = 3
MODEL_RETRY_BASE_DELAY_SECONDS = 2.0


def chat_payload(
    model: str,
    *,
    temperature: float,
    messages: list[dict[str, Any]],
    json_object: bool = False,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096 if json_object else 512,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if "gemini" in model.lower():
        payload["max_completion_tokens"] = payload["max_tokens"]
    if json_object and "gemini" not in model.lower():
        payload["response_format"] = {"type": "json_object"}
    return payload


async def request_completion(url: str, api_key: str, payload: dict[str, Any], timeout: float) -> str:
    response = await post_completion(url, api_key, payload, timeout)
    try:
        content = completion_content(response)
        if content.strip():
            return content
    except ValueError as exc:
        if not is_no_text_output_error(exc):
            raise
        last_error = exc
    else:
        last_error = empty_completion_error(response)

    retry_payload = retry_chat_payload(payload)
    for attempt in range(1, MODEL_EMPTY_OUTPUT_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(MODEL_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 2)))
        retry_response = await post_completion(url, api_key, retry_payload, timeout)
        try:
            content = completion_content(retry_response)
            if content.strip():
                return content
        except ValueError as exc:
            if not is_no_text_output_error(exc):
                raise
            last_error = exc
        else:
            last_error = empty_completion_error(retry_response)
    raise last_error


def is_no_text_output_error(exc: ValueError) -> bool:
    return "no text output" in str(exc)


def empty_completion_error(response: httpx.Response) -> ValueError:
    return ValueError(f"Model API returned no text output. Body: {response.text[:500]}")


def retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return MODEL_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))


async def post_completion(url: str, api_key: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, MODEL_RETRY_ATTEMPTS + 1):
            try:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
            except httpx.TransportError:
                if attempt >= MODEL_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(MODEL_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
                continue
            if (
                response.status_code in TRANSIENT_MODEL_STATUSES
                and attempt < MODEL_RETRY_ATTEMPTS
            ):
                await asyncio.sleep(retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response
    raise RuntimeError("Model request retry loop exited unexpectedly.")


def retry_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retry_payload = {**payload}
    max_tokens = int(retry_payload.get("max_tokens") or 4096)
    retry_payload["max_tokens"] = max(max_tokens, 8192)
    if "gemini" in str(retry_payload.get("model", "")).lower():
        retry_payload["max_completion_tokens"] = retry_payload["max_tokens"]
    messages = retry_payload.get("messages")
    if isinstance(messages, list) and len(messages) >= 2 and messages[0].get("role") == "system":
        system_text = str(messages[0].get("content") or "")
        user_message = {**messages[1]}
        user_content = user_message.get("content")
        if isinstance(user_content, list):
            user_message["content"] = [{"type": "text", "text": system_text}, *user_content]
        elif isinstance(user_content, str):
            user_message["content"] = f"{system_text}\n\n{user_content}"
        retry_payload["messages"] = [user_message, *messages[2:]]
    return retry_payload


def completion_content(response: httpx.Response) -> str:
    try:
        response_data = response.json()
    except JSONDecodeError:
        return streamed_completion_content(response.text)
    if not isinstance(response_data, dict):
        raise ValueError(f"Model API returned unexpected JSON: {str(response_data)[:500]}")
    if response_data.get("error"):
        raise ValueError(f"Model API returned an error: {str(response_data['error'])[:500]}")

    choices = response_data.get("choices")
    if isinstance(choices, list) and choices:
        return choice_content(choices[0])

    choice = response_data.get("choice")
    if isinstance(choice, dict):
        return choice_content(choice)

    for key in ("content", "text", "output_text", "response"):
        value = response_data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = nested_response_content(value)
            if nested:
                return nested
            if key == "response":
                usage = value.get("usageMetadata")
                raise ValueError(
                    "Model API returned no text output in response. "
                    f"Usage metadata: {usage}. Body: {str(response_data)[:500]}"
                )

    raise ValueError(
        "Model API response did not include choices/message content. "
        f"Response keys: {', '.join(response_data.keys())}. Body: {str(response_data)[:500]}"
    )


def choice_content(choice: dict[str, Any]) -> str:
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
        return str(content or "")
    if isinstance(choice.get("delta"), dict):
        return str(choice["delta"].get("content") or "")
    if isinstance(choice.get("text"), str):
        return str(choice["text"])
    if isinstance(choice.get("content"), str):
        return str(choice["content"])
    raise ValueError(f"Model API choice did not include message content: {str(choice)[:500]}")


def nested_response_content(response_data: dict[str, Any]) -> str:
    candidates = response_data.get("candidates")
    if isinstance(candidates, list) and candidates:
        candidate = candidates[0]
        if isinstance(candidate, dict):
            content = candidate.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
                if isinstance(content.get("text"), str):
                    return str(content["text"])
            if isinstance(candidate.get("text"), str):
                return str(candidate["text"])
    for key in ("text", "outputText", "output_text"):
        value = response_data.get(key)
        if isinstance(value, str):
            return value
    return ""


def streamed_completion_content(text: str) -> str:
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except JSONDecodeError as exc:
            raise ValueError(f"Model API returned invalid JSON event: {data[:500]}") from exc
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            chunks.append(str(delta.get("content") or ""))
    if not chunks:
        raise ValueError(f"Model API returned invalid JSON: {text[:500]}")
    return "".join(chunks)


def extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Model returned an empty response body.")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    if not text:
        raise ValueError("Model response did not contain JSON content.")
    return json.loads(text)
