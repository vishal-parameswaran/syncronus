import logging

from rich.logging import RichHandler

from rich.console import Console

console = Console(
    width=100,
    highlight=False,
    markup=True,
    soft_wrap=True,
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        RichHandler(
            console=console,
            rich_tracebacks=True,
            markup=True,
        )
    ],
    force=True,
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
