"""Compatibilidade: src canônico na raiz do repositório."""
from pathlib import Path
__path__ = [str(Path(__file__).resolve().parents[2] / "src")]
