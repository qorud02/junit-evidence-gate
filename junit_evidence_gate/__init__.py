"""Policy checks for evidence recorded in JUnit XML reports."""

from .core import Policy, Report, inspect_reports
from .github import render_github

__all__ = ["Policy", "Report", "inspect_reports", "render_github"]
__version__ = "0.2.1"
