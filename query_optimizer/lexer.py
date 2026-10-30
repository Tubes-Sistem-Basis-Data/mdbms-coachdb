"""QO-01: Tokenisasi dan Penanganan Kesalahan Sintaks.

PIC: QO1 (Angelina / ketua group)
Status: STUB — menunggu implementasi.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterator

from common.errors import QuerySyntaxError


# ==========================================
# Token Types
# ==========================================

class TokenType(Enum):
    """Jenis token SQL."""

    KEYWORD = auto()
    IDENT = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    OP = auto()
    COMMA = auto()
    DOT = auto()
    LPAREN = auto()
    RPAREN = auto()
    SEMICOLON = auto()
    EOF = auto()


@dataclass
class Token:
    """Satu token hasil tokenisasi."""

    type: TokenType
    value: str | int | float
    line: int = 1
    col: int = 0


# ==========================================
# Token Stream
# ==========================================

class TokenStream:
    """Stream token yang mendukung peek dan next."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def peek(self) -> Token:
        """Lihat token berikutnya tanpa mengonsumsi."""
        if self._pos >= len(self._tokens):
            return Token(TokenType.EOF, "", 0, 0)
        return self._tokens[self._pos]

    def next(self) -> Token:
        """Konsumsi dan kembalikan token berikutnya."""
        tok = self.peek()
        if tok.type != TokenType.EOF:
            self._pos += 1
        return tok

    def expect(self, token_type: TokenType, value: str | None = None) -> Token:
        """Konsumsi token berikutnya, raise jika tipe/nilai tidak cocok."""
        tok = self.next()
        if tok.type != token_type:
            raise QuerySyntaxError(
                f"Diharapkan {token_type.name}, ditemukan {tok.type.name} '{tok.value}'",
                position=tok.col,
                token=str(tok.value),
            )
        if value is not None and str(tok.value).upper() != value.upper():
            raise QuerySyntaxError(
                f"Diharapkan '{value}', ditemukan '{tok.value}'",
                position=tok.col,
                token=str(tok.value),
            )
        return tok

    def is_eof(self) -> bool:
        """Apakah stream sudah habis."""
        return self.peek().type == TokenType.EOF


# ==========================================
# Lexer
# ==========================================

class Lexer:
    """Mengubah teks SQL menjadi stream token.

    TODO (QO1): Implementasikan tokenisasi lengkap.
    """

    def tokenize(self, sql: str) -> TokenStream:
        """Tokenisasi teks SQL.

        Raise QuerySyntaxError jika ditemukan karakter ilegal.
        """
        # STUB: menunggu implementasi QO1
        raise NotImplementedError("Lexer.tokenize belum diimplementasi (QO-01)")

