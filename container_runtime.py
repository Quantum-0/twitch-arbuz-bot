from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from container import Container

_container: "Container | None" = None


def set_container(container: "Container | None") -> None:
    global _container
    _container = container


def get_container() -> "Container":
    if _container is None:
        raise RuntimeError("Container was not initialized")
    return _container
