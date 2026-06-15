"""Guard against the env-wiring landmine: a security/daemon knob read by the app
but NOT listed in docker-compose.yml `environment:` silently uses its default in
the container (this exact bug shipped — JWT_SECRET stayed at the dev default).
Pure file read, no DB, no app import."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# knobs that MUST reach the container (security + daemon toggles)
MUST_WIRE = {
    "JWT_SECRET", "APP_ENV", "CORS_ALLOW_ORIGINS",
    "EMBED_TOKEN_TTL_MIN", "EMBED_PRUNE_ENABLED",
    "VERIFY_ENABLED", "VERIFY_FLAG_THRESHOLD",
    "DB_MAINTENANCE_ENABLED", "USAGE_ROLLUP_ENABLED",
    "AUTO_LEARN_ENABLED", "AUTO_LEARN_MIN_CONF", "AUTO_APPROVE_MIN_CONF",
}


def test_critical_env_wired_in_compose():
    compose = (ROOT / "docker-compose.yml").read_text()
    missing = sorted(k for k in MUST_WIRE if k not in compose)
    assert not missing, (
        "config knobs read by the app but NOT wired into docker-compose.yml "
        f"environment (they will silently use defaults in the container): {missing}"
    )
