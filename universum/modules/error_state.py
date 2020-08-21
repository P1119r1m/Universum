from typing import List
import inspect

from ..lib.gravity import Module, Dependency


class GlobalErrorState(Module):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)  # type: ignore
        self.errors: List[str] = []

    def get_errors(self):
        return self.errors

    def is_in_error_state(self):
        return len(self.errors) > 0


class HasErrorState(Module):
    global_error_state_factory = Dependency(GlobalErrorState)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)  # type: ignore
        self.global_error_state = self.global_error_state_factory()

    def error(self, message: str):
        self.global_error_state.errors.append(inspect.cleandoc(message))

    def is_in_error_state(self):
        return self.global_error_state.is_in_error_state()