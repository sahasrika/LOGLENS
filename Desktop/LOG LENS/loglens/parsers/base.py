"""
Abstract base class for all LogLens log parsers.

Every concrete parser must implement `parse`, which accepts the full raw
text of a log file (or pasted snippet) and returns a list of ParsedLog
objects — one per logical log entry.

Design notes
------------
- A "logical log entry" may span multiple physical lines (e.g. a stack trace).
- The parser is responsible for joining continuation lines to their parent entry.
- Parsers must never raise exceptions for malformed input; instead they should
  return a ParsedLog with only the `raw` field populated so the pipeline can
  continue processing the remaining lines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from loglens.models import ParsedLog


class BaseParser(ABC):
    """Abstract interface for all log parsers."""

    @abstractmethod
    def parse(self, text: str) -> list[ParsedLog]:
        """
        Parse *text* and return one ParsedLog per logical log entry.

        Parameters
        ----------
        text:
            Raw log text — may contain any number of lines, including
            multi-line stack traces.

        Returns
        -------
        list[ParsedLog]
            Ordered list of parsed entries.  Never raises; returns at
            minimum a single ParsedLog(raw=text) for completely
            unparseable input.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable parser name, defaults to the class name."""
        return self.__class__.__name__
