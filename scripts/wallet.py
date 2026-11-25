"""Load a Quorum wallet, from tests/ or from the environment.

Local runs read `tests/tester1-keystore.json` and `tests/tester1.password`, which
are never tracked. CI reads the same values from repository secrets.

A named wallet is looked up under `QUORUM_TESTER2_KEYSTORE` first and then under
the same pair as it was written in the sibling repositories (`ASSIZE_…`,
`AUSPICE_…`), so a secret copied between repositories still works. It never
falls back to the deployer for a named wallet.
"""

import base64
import json
import os
from pathlib import Path

from eth_account import Account

DEFAULT_FOLDER = Path(__file__).resolve().parents[1] / "tests"
DEFAULT_WALLET = "tester1"

PREFIXES = ("QUORUM", "ASSIZE", "AUSPICE")
DEPLOYER_ALIASES = ("QUORUM_DEPLOYER", "ASSIZE_DEPLOYER", "AUSPICE_DEPLOYER")


def wallet_folder() -> Path:
    configured = os.environ.get("QUORUM_WALLET_FOLDER")
    return Path(configured) if configured else DEFAULT_FOLDER


def _env_names(name: str, suffix: str) -> list:
    """Every environment name this wallet may be filed under."""
    upper = name.upper()
    stem = upper.rstrip("0123456789")
    digits = upper[len(stem) :]

    names = []
    for prefix in PREFIXES:
        names.append(f"{prefix}_{upper}_{suffix}")
        if digits:
            names.append(f"{prefix}_{stem}_{suffix}{digits}")
            names.append(f"{prefix}_{stem}_{digits}_{suffix}")

    if name == DEFAULT_WALLET:
        for alias in DEPLOYER_ALIASES:
            names.append(f"{alias}_{suffix}")

    return names


def _first_env(names) -> str:
    for key in names:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def named_env_present(name: str) -> bool:
    """Whether this named wallet has a keystore of its own in the environment."""
    return bool(_first_env(_env_names(name, "KEYSTORE")))


def _keystore_from_env(name: str):
    raw = _first_env(_env_names(name, "KEYSTORE"))
    if not raw:
        return None

    payload = raw
    if not raw.lstrip().startswith("{"):
        try:
            payload = base64.b64decode(raw).decode("utf-8")
        except Exception as error:
            raise SystemExit(f"[wallet] {name}: keystore is neither JSON nor base64") from error
    try:
        return json.loads(payload)
    except Exception as error:
        raise SystemExit(f"[wallet] {name}: keystore is not a keystore") from error


def load_account(name: str = DEFAULT_WALLET):
    """Decrypt one keystore (file or environment) and return the account."""
    folder = wallet_folder()
    keystore_path = folder / f"{name}-keystore.json"
    password_path = folder / f"{name}.password"

    password = ""
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").strip()

    env_password = _first_env(_env_names(name, "PASSWORD"))
    if env_password:
        password = env_password

    if keystore_path.exists():
        data = json.loads(keystore_path.read_text(encoding="utf-8"))
    else:
        data = _keystore_from_env(name)

    if data is None:
        raise SystemExit(
            f"no wallet '{name}' in {folder} and no "
            + " or ".join(_env_names(name, "KEYSTORE"))
        )

    if not password:
        raise SystemExit(
            f"[wallet] {name}: no password (tests/{name}.password, "
            + " or ".join(_env_names(name, "PASSWORD"))
            + ")"
        )

    return Account.from_key(Account.decrypt(data, password))
