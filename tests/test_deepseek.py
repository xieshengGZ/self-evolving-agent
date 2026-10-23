from app import deepseek


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self.text = content
        self._content = content

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class FakeClient:
    last_payload = None

    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        FakeClient.last_payload = kwargs.get("json")
        return self.response


def test_chat_text_returns_raw_prose(monkeypatch):
    content = "SSE 是服务器推送，示例：{\"type\":\"token\",\"text\":\"你\"}"
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kwargs: FakeClient(FakeResponse(content)))
    assert deepseek.chat_text([{"role": "user", "content": "x"}]) == content


def test_chat_extracts_action_json(monkeypatch):
    content = "说明文字 {\"action\":\"done\",\"message\":\"ok\"} 结尾"
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kwargs: FakeClient(FakeResponse(content)))
    assert deepseek.chat([{"role": "user", "content": "x"}]) == {
        "action": "done",
        "message": "ok",
    }


def test_chat_requests_json_output(monkeypatch):
    content = '{"action":"done","message":"ok"}'
    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kwargs: FakeClient(FakeResponse(content)))
    assert deepseek.chat([{"role": "user", "content": "return json"}])["action"] == "done"
    assert FakeClient.last_payload["response_format"] == {"type": "json_object"}


def test_chat_falls_back_when_json_mode_unsupported(monkeypatch):
    content = '{"action":"done","message":"ok"}'
    payloads = []

    class SequenceClient(FakeClient):
        calls = 0

        def post(self, *args, **kwargs):
            payloads.append(kwargs.get("json"))
            SequenceClient.calls += 1
            if SequenceClient.calls == 1:
                return FakeResponse("unsupported", status_code=400)
            return self.response

    monkeypatch.setattr(deepseek.httpx, "Client", lambda **kwargs: SequenceClient(FakeResponse(content)))
    assert deepseek.chat([{"role": "user", "content": "return json"}])["action"] == "done"
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
