from .avito import AvitoSource
from .base import Source
from .csv_file import CsvSource
from .youla import YoulaSource

SOURCE_TITLES = {"avito": "Авито", "youla": "Юла", "csv": "CSV"}

__all__ = ["AvitoSource", "CsvSource", "SOURCE_TITLES", "Source", "YoulaSource"]
