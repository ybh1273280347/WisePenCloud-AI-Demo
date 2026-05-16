class FileHandoffError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class FileHandoffWriteError(FileHandoffError):
    def __init__(self, message: str):
        super().__init__(message)


class FileHandoffInvalidSuffixError(FileHandoffError):
    def __init__(self, suffix: str):
        self.suffix = suffix
        super().__init__(f"Unsupported handoff file suffix: {suffix}")
