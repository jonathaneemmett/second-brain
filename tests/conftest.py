import pytest

from second_brain.vault import DAILY, NOTES, PEOPLE, Note, Vault


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault")
    v.init()
    return v


@pytest.fixture
def seeded(vault):
    notes = {
        f"{NOTES}/Pricing Ideas.md": "Annual billing at the top of pricing.",
        f"{NOTES}/Pricing Experiments.md": "Annual billing toggle. See [[Pricing Ideas]].",
        f"{PEOPLE}/Dana.md": "Customer success lead.",
        f"{DAILY}/2026-11-17.md": "Talked to Dana. Also see [[Pricing Experiments]].",
    }
    for rel, body in notes.items():
        vault.write(Note(path=rel, body=body, meta={"tags": ["x"]}))
    vault.commit("seed")
    return vault
