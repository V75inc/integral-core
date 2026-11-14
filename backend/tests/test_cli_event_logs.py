"""Managed event worker logs are accessible through the public CLI."""

from app.cli import main


def test_logs_events_reads_only_selected_worker_log(tmp_path, capsys):
    """Read the requested event log tail without mixing API output."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.log").write_text("old event\nlatest event\n")
    (logs / "api.log").write_text("API output\n")
    assert (
        main(["logs", "--home", str(tmp_path), "--service", "events", "--lines", "1"])
        == 0
    )
    output = capsys.readouterr()
    assert output.out == "latest event\n"
    assert output.err == ""


def test_logs_events_reports_missing_log(tmp_path, capsys):
    """Report missing event logs rather than rejecting the service option."""
    assert main(["logs", "--home", str(tmp_path), "--service", "events"]) != 0
    assert "No events log exists" in capsys.readouterr().err
