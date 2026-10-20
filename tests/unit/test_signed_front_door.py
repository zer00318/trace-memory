from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONT = (ROOT / "trace").read_text()


def test_start_cannot_bypass_the_signed_helper_with_repo_python():
    assert 'nohup "$HELPER" --daemon' in FRONT
    assert 'nohup "$PY" -m tracemem' not in FRONT
    assert "codesign --verify --deep --strict" in FRONT


def test_sensitive_offline_routes_cross_the_same_helper_boundary():
    for route in ("--ingest", "--export", "--encrypt", "--settings",
                  "--expect", "--reconcile-boundaries"):
        line = next(line for line in FRONT.splitlines() if route in line and
                    not line.lstrip().startswith("#"))
        assert '"$HELPER" --trace' in line


def test_install_orders_build_then_key_migration_then_launchagent():
    start = FRONT.index("  install)")
    end = FRONT.index("  uninstall)", start)
    install = FRONT[start:end]
    assert install.index("make_app.py") < install.index("migrate_scoped_key.py")
    assert install.index("migrate_scoped_key.py") < install.index("--cold-start")
    assert install.index("--cold-start") < install.index("install_agent.py")
    assert install.index("migrate_scoped_key.py") < install.index("install_agent.py")


def test_install_cold_start_is_owned_by_the_signed_helper():
    start = FRONT.index("  install)")
    end = FRONT.index("  uninstall)", start)
    install = FRONT[start:end]
    assert '"$HELPER" --cold-start' in install
    assert '"$HELPER" --trace --ingest "$2"' in install
    assert "osascript" not in install
