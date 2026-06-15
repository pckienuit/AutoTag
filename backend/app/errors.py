import httpx


def error_detail(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message

    if isinstance(exc, httpx.TimeoutException):
        return httpx_request_error_detail(exc, "request timed out")

    if isinstance(exc, httpx.HTTPError):
        return httpx_request_error_detail(exc, "request failed")

    cause = getattr(exc, "__cause__", None)
    if cause is not None and str(cause).strip():
        return f"{exc.__class__.__name__}: {str(cause).strip()}"

    return exc.__class__.__name__


def httpx_request_error_detail(exc: httpx.HTTPError, fallback: str) -> str:
    request = getattr(exc, "request", None)
    if request is None:
        return f"{exc.__class__.__name__}: {fallback}"
    method = str(getattr(request, "method", "") or "").upper()
    url = str(getattr(request, "url", "") or "")
    target = " ".join(part for part in (method, url) if part)
    if not target:
        return f"{exc.__class__.__name__}: {fallback}"
    return f"{exc.__class__.__name__}: {fallback} while calling {target}"
