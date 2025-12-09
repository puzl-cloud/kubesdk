from typing import Generic, TypeVar

_ErrorExtraT = TypeVar('_ErrorExtraT')


class RESTAPIError(Exception, Generic[_ErrorExtraT]):
    """General exception for REST API errors."""
    api_name: str
    status: int
    _message: str
    response: dict | str
    extra: _ErrorExtraT | None

    def __init__(self, status: int = None, message: str = None, response: dict | str = None,
                 api_name: str = "Kubernetes", extra: _ErrorExtraT = None):
        super().__init__(message)
        self.status = status
        self._message = message
        self.response = response
        self.api_name = api_name
        self.extra = extra

    def __str__(self): return f"{self.api_name} Error {self.status}: {self._message}. Response: {self.response}"


class BadRequestError(RESTAPIError): status = 400
class UnauthorizedError(RESTAPIError): status = 401
class ForbiddenError(RESTAPIError): status = 403
class NotFoundError(RESTAPIError): status = 404
class MethodNotAllowedError(RESTAPIError): status = 405
class ConflictError(RESTAPIError): status = 409
class GoneError(RESTAPIError): status = 410
class UnsupportedMediaType(RESTAPIError): status = 415
class UnprocessableEntityError(RESTAPIError): status = 422
class TooManyRequestsError(RESTAPIError): status = 429
class InternalServerError(RESTAPIError): status = 500
class ServiceUnavailableError(RESTAPIError): status = 503
class ServerTimeoutError(RESTAPIError): status = 504


ERROR_TYPE_BY_CODE = {
    400: BadRequestError,
    401: UnauthorizedError,
    403: ForbiddenError,
    404: NotFoundError,
    405: MethodNotAllowedError,
    409: ConflictError,
    410: GoneError,
    415: UnsupportedMediaType,
    422: UnprocessableEntityError,
    429: TooManyRequestsError,
    500: InternalServerError,
    503: ServiceUnavailableError,
    504: ServerTimeoutError
}


class LoginError(Exception): """ Raised when the client cannot login to the API. """
