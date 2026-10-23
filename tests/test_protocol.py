from app.protocol import validate_action


def test_action_envelope_preserves_tool_fields():
    action, error = validate_action({"action": " shell ", "command": "ls"})
    assert error == ""
    assert action == {"action": "shell", "command": "ls"}


def test_action_envelope_rejects_missing_or_non_string_action():
    assert validate_action({"command": "ls"})[1]
    assert validate_action({"action": ""})[1]
    assert validate_action({"action": 1})[1]
    assert validate_action(["done"])[1]
