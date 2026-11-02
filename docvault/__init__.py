"""DocVault: search all your documents offline."""
from .extract import extract, read_docx, read_pdf, read_xlsx
from .vault import Vault, signature, similarity
from .web import create_app

__all__ = ["Vault", "create_app", "extract", "read_docx", "read_pdf", "read_xlsx", "signature", "similarity"]
