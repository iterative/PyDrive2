import json
from urllib.parse import parse_qs, urlsplit


def response(data):
    return {"status": "200"}, json.dumps(data)


def error_response(code, reason):
    return {"status": str(code)}, json.dumps(
        {"error": {"code": code, "errors": [{"reason": reason}]}}
    )


def query(request):
    return parse_qs(urlsplit(request[0]).query)
