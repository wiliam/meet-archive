"""Регрессии запуска локальной админки."""

from pathlib import Path


def test_serve_invokes_uvicorn_as_python_module():
    """Не полагаемся на console-script с длинным shebang пути репозитория."""
    script = (Path(__file__).resolve().parents[2] / "serve.sh").read_text()

    assert 'nohup "$VENV/bin/python" -m uvicorn' in script
    assert 'exec "$VENV/bin/python" -m uvicorn' in script
    assert "app)    cmd_app" in script


def test_app_launcher_keeps_server_in_foreground_for_tcc():
    installer = (Path(__file__).resolve().parents[2] / "install-app.sh").read_text()

    assert 'const char *mode = (argc > 1) ? argv[1] : "app";' in installer


def test_setup_rebuilds_venv_when_homebrew_python_disappears():
    setup = (Path(__file__).resolve().parents[2] / "setup.sh").read_text()

    assert '[[ ! -x "$VENV/bin/python" ]]' in setup
    assert 'python3 -m venv --clear "$VENV"' in setup
    assert '"$VENV/bin/python" -m pip install' in setup
