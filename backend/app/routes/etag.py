"""Answer polled endpoints with 304 when nothing changed since the caller's last copy."""

import hashlib
import json

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder


def etag_response(request: Request, payload) -> Response:
    body = json.dumps(jsonable_encoder(payload), separators=(",", ":"), sort_keys=True).encode()
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)
