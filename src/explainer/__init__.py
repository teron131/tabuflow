"""File explanation services for the workbench."""

from .explainer import MissingExplainerModelError, explain_file

__all__ = ["MissingExplainerModelError", "explain_file"]
