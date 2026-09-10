class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        *,
        details: dict | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        self.retryable = retryable


def not_found() -> AppError:
    return AppError("NOT_FOUND", "内容不存在或你没有访问权限。", 404)


def conflict(message: str = "内容已发生变化，请刷新后重新确认。") -> AppError:
    return AppError("VERSION_CONFLICT", message, 409)
