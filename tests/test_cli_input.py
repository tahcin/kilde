"""Input reading: invalid organisation numbers get their own failed envelope."""
from kilde import cli


def test_bad_check_digit_is_invalid_input(tmp_path):
    p = tmp_path / "in.jsonl"
    p.write_text('{"organisation_number":"123456789"}\n{"organisation_number":"923609016"}\n'
                 '{"organisation_number":"NO 923 609 016 MVA"}\nabc\n', encoding="utf-8")
    assert cli.read_organisations(str(p)) == ["923609016"]
    assert cli.INVALID == ["123456789", "abc"]
    env = cli.invalid_envelope("123456789", "r", "2026-10-14T00:00:00Z")
    assert env["run"]["terminal_status"] == "failed" and "check digit" in env["errors"][0]["message"]
