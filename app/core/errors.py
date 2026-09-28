from fastapi import HTTPException


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail=message, headers={"X-Error-Code": code})
