class ResyAPIError(Exception):
    def __init__(
        self,
        message: str,
        status: int,
        code: int | None = None,
        raw_body: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.raw_body = raw_body
