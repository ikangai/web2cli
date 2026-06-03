"""The 5 new modules must ship in both bundlers, and both tray apps must tear
down stray wcb_* tmux servers on quit (otherwise a quit leaks detached claude
sessions that outlive the bridge)."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_MODULES = ["paths", "fsm", "tmux_session", "session_registry",
               "session_endpoints"]


def test_setup_py_bundles_new_modules():
    text = (ROOT / "setup.py").read_text()
    for m in NEW_MODULES:
        assert ('"%s"' % m) in text, "setup.py must bundle %s" % m


def test_pyinstaller_spec_hiddenimports_new_modules():
    text = (ROOT / "pyinstaller-win.spec").read_text()
    for m in NEW_MODULES:
        assert ('"%s"' % m) in text, "pyinstaller spec must hidden-import %s" % m


def test_both_tray_apps_kill_all_on_quit():
    for app in ("bridge_app.py", "bridge_app_win.py"):
        text = (ROOT / app).read_text()
        assert "kill_all_wcb()" in text, "%s must kill wcb_* servers on quit" % app
        assert "session_registry" in text, "%s must import session_registry" % app
