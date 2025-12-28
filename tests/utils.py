import json

class FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, headers=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}
        self.text = text or json.dumps(self._json_data)

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}: {self.text}")
