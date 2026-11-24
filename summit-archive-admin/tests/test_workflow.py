"""Exercise the meeting workflow from the CLI using synthetic recordings."""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import meet_archive_cli as cli
from test_reader import reader_db  # noqa: F401


@pytest.fixture
def workflow(tmp_path, tmp_path_factory, reader_db, monkeypatch, capsys):
    archive, _ = reader_db
    team = tmp_path_factory.mktemp("workflow-team")
    project = team / "projects" / "Billing" / "Redesign"
    (project / "meetings").mkdir(parents=True)
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    config = {
        "file_sources": {"hh": str(team), "inbox": str(incoming)},
        "team_root": str(team),
    }
    config_file = tmp_path / "workflow.json"
    config_file.write_text(json.dumps(config))
    monkeypatch.setenv("SUMMIT_ARCHIVE_ROOT", str(archive.parent))
    monkeypatch.setenv("MEET_ARCHIVE_WORKFLOW_CONFIG", str(config_file))

    def run(*args, error=None):
        status = cli.main(["workflow", *map(str, args)])
        output = capsys.readouterr()
        if error:
            assert status == 2
            assert error in output.err
        else:
            assert status == 0, output.err
            return json.loads(output.out)

    return run, project, incoming, config_file


def test_summit_export_requires_review_and_keeps_personal_meetings_private(workflow, tmp_path):
    run, project, _, _ = workflow
    draft = run("prepare", "--summit", "D1", "--template", "decisions")
    assert "Обсудили метрики биллинга" in Path(draft["prompt_file"]).read_text()
    assert "Обсудили метрики биллинга" in Path(draft["transcript_file"]).read_text()
    assert "Distinguish proposals" in Path(draft["prompt_file"]).read_text()
    edited = tmp_path / "draft.md"
    edited.write_text("# Decisions\n\n- Launch the new billing API.\n")
    run("save", draft["id"], edited)

    run("classify", "D1", "--private")
    run("publish", draft["id"], "--project", "Billing/Redesign", error="as work")
    run("classify", "D1", "--project", "Billing/Redesign", "--summit-only")
    preview = run("publish", draft["id"])
    assert len(preview["files"]) == 1
    assert not list((project / "meetings").iterdir())
    edited.write_text("# Decisions\n\n- Keep the existing API.\n")
    run("save", draft["id"], edited)
    run("publish", draft["id"], "--confirm", preview["confirmation"], error="Publication has changed")
    assert not list((project / "meetings").iterdir())

    preview = run("publish", draft["id"], "--include-transcript")
    applied = run("publish", draft["id"], "--include-transcript", "--confirm", preview["confirmation"])
    assert applied["published"]
    assert Path(applied["files"][0]["to"]).read_text() == edited.read_text()
    assert "Обсудили метрики биллинга" in Path(applied["files"][1]["to"]).read_text()
    run("publish", draft["id"], "--include-transcript", "--confirm", preview["confirmation"])
    assert len(list((project / "meetings").iterdir())) == 2


def test_late_corporate_transcript_is_linked_without_changing_published_summary(workflow, tmp_path):
    run, project, incoming, _ = workflow
    draft = run("prepare", "--summit", "D1")
    summary = tmp_path / "summary.md"
    summary.write_text("# Fast Summit summary\n")
    run("save", draft["id"], summary)
    run("classify", "D1", "--project", "Billing/Redesign")
    run("publish", draft["id"], "--include-transcript", error="Summit-only")
    preview = run("publish", draft["id"])
    run("publish", draft["id"], "--confirm", preview["confirmation"])

    day = datetime.fromtimestamp(2000).date().isoformat()
    late = incoming / f"[{day}]-Billing redesign-transcript.md"
    late.write_text("Corporate transcript arrives later.\n")
    meeting = run("inspect", "D1")
    assert meeting["linked_files"] == []
    assert meeting["candidates"][0]["file"] == str(late)
    run("classify", "D1", "--project", "Billing/Redesign", "--summit-only")
    run("publish", draft["id"], "--include-transcript", error="potential corporate match")
    run("link", "D1", late)
    meeting = run("inspect", "D1")
    assert meeting["linked_files"] == [f"inbox:{late.name}"]
    assert meeting["candidates"] == []
    run("publish", draft["id"], "--include-transcript", error="corporate recording is linked")
    assert Path(preview["files"][0]["to"]).read_text() == summary.read_text()
    assert len(list((project / "meetings").iterdir())) == 1


def test_corporate_template_publishes_next_to_existing_project_file_and_routes_inbox(workflow, tmp_path):
    run, project, incoming, config_file = workflow
    template = tmp_path / "metrics.md"
    template.write_text("Report outcomes by measurement period.\n")
    config = json.loads(config_file.read_text())
    config["templates"] = {"metrics": template.name}
    config["summarizer"] = [sys.executable, "-c", "import sys; print('# Metrics'); assert '10% growth' in sys.stdin.read()"]
    config_file.write_text(json.dumps(config))
    transcript = project / "meetings" / "2026-11-18.metrics.transcript.txt"
    transcript.write_text("10% growth confirmed.\n")

    draft = run("prepare", "--corporate", transcript, "--template", "metrics", "--generate")
    assert draft["project"] == "Billing/Redesign"
    assert Path(draft["transcript_file"]) == transcript
    assert "Report outcomes" in Path(draft["prompt_file"]).read_text()
    preview = run("publish", draft["id"])
    run("publish", draft["id"], "--confirm", preview["confirmation"])
    target = Path(preview["files"][0]["to"])
    assert target.parent == transcript.parent
    assert target.read_text() == "# Metrics\n"
    assert transcript.read_text() == "10% growth confirmed.\n"
    run("publish", draft["id"], "--include-transcript", error="already stored")

    inbox_file = incoming / "2026-11-19-transcript.txt"
    inbox_file.write_text(transcript.read_text())
    inbox_draft = run("prepare", "--corporate", inbox_file, "--generate")
    run("publish", inbox_draft["id"], error="Select the team project")
    preview = run("publish", inbox_draft["id"], "--project", "Billing/Redesign")
    assert Path(preview["files"][0]["to"]).parent == transcript.parent


def test_automatic_rules_prepare_only_selected_summit_series(workflow, reader_db):
    run, project, _, config_file = workflow
    _, conn = reader_db
    conn.execute("UPDATE documents SET created_date = ?", (time.time(),))
    conn.commit()
    config = json.loads(config_file.read_text())
    config["summarizer"] = [sys.executable, "-c", "print('# Draft decisions')"]
    config["rules"] = [{"title_prefix": "Billing", "tag": "project-x", "project": "Billing/Redesign", "template": "decisions"}]
    config_file.write_text(json.dumps(config))
    draft, = run("auto")
    assert draft["key"] == "D1"
    assert draft["template"] == "decisions"
    assert run("auto")[0]["id"] == draft["id"]
    assert run("inspect", "D1")["project"] == "Billing/Redesign"
    assert run("inspect", "D2")["classification"] == "unclassified"
    assert not list((project / "meetings").iterdir())
    run("classify", "D1", "--private")
    assert run("auto") == []


def test_private_work_directory_cannot_be_inside_team_root(workflow):
    run, project, _, config_file = workflow
    config = json.loads(config_file.read_text())
    drafts = project / "drafts"
    config["work_dir"] = str(drafts)
    config_file.write_text(json.dumps(config))
    run("prepare", "--summit", "D1", error="must be separate")
    assert not drafts.exists()
