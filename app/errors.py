class BusinessError(ValueError):
    def __init__(self, code, message, status=422, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def object_payload(value):
    if not isinstance(value, dict):
        raise BusinessError("PAYLOAD", "请求须为JSON对象")
    return value
