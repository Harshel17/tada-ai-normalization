class NormalizationError(Exception):
    """The payload cannot be normalized at all (wrong shape, missing required context)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
