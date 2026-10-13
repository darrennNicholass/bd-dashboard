"""In-memory Google Sheets doubles with request counters. No network access."""
import copy
import threading

import gspread

from src.snd_cloud_store import TAB

BOOKS: dict = {}  # Shared with tests/snd_ui_harness.py across AppTest reruns.


def api_error(code: int) -> gspread.exceptions.APIError:
    class Response:
        status_code = code
        text = "fake error"

        def json(self):
            return {"error": {"code": code, "message": "fake", "status": "FAKE"}}

    return gspread.exceptions.APIError(Response())


class FakeWorksheet:
    def __init__(self, title, values=None):
        self.title = title
        self.values = copy.deepcopy(values or [])
        self.reads = 0
        self.appends = 0
        self.read_errors = []    # Raised (in order) before a read is served.
        self.append_errors = []  # (exception, committed) pairs for the next appends.
        self.lock = threading.Lock()

    def get_all_values(self, **kwargs):
        with self.lock:
            self.reads += 1
            if self.read_errors:
                raise self.read_errors.pop(0)
            return copy.deepcopy(self.values)

    def append_row(self, values, value_input_option, insert_data_option):
        self.append_rows([values], value_input_option, insert_data_option)

    def append_rows(self, values, value_input_option, insert_data_option):
        assert value_input_option == "RAW" and insert_data_option == "INSERT_ROWS"
        with self.lock:
            self.appends += 1
            error, committed = self.append_errors.pop(0) if self.append_errors else (None, False)
            if error is None or committed:
                self.values.extend(copy.deepcopy(values))
        if error is not None:
            raise error

    def update(self, *args, **kwargs):
        raise AssertionError("S&D storage must never overwrite existing cells")

    def clear(self, *args, **kwargs):
        raise AssertionError("S&D storage must never clear the worksheet")


class FakeBook:
    def __init__(self, config_values=None, *, with_config=False):
        self.tabs = {"Sheet1": FakeWorksheet("Sheet1", [["keep me"]])}
        if with_config:
            self.tabs[TAB] = FakeWorksheet(TAB, config_values)
        self.metadata_reads = 0
        self.created = 0
        self.lock = threading.Lock()

    @property
    def config(self):
        return self.tabs.get(TAB)

    def worksheet(self, title):
        with self.lock:
            self.metadata_reads += 1
            if title not in self.tabs:
                raise gspread.exceptions.WorksheetNotFound(title)
            return self.tabs[title]

    def worksheets(self):
        with self.lock:
            return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        with self.lock:
            if title in self.tabs:
                raise RuntimeError("duplicate title")
            self.created += 1
            self.tabs[title] = FakeWorksheet(title)
            return self.tabs[title]

    def writes(self) -> int:
        return self.created + sum(tab.appends for tab in self.tabs.values())
