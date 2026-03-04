from common.core.domain import IErrorCode


class ServiceException(Exception):
    """业务异常基类"""
    def __init__(self, error_code: IErrorCode, custom_msg: str = None):
        self.code = error_code.code
        self.msg = custom_msg if custom_msg else error_code.msg
        super().__init__(self.msg)