from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
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
    type: TokenType
    value: str | int | float
    line: int
    col: int


class TokenStream:
    """Aliran token hasil Lexer yang dibaca parser satu per satu."""

    def __init__(self, tokens: list[Token] | None = None) -> None:
        self._tokens: list[Token] = tokens or []
        self._pos: int = 0

    def peek(self, offset: int = 0) -> Token:
        """Lihat token di posisi sekarang + offset tanpa menggeser posisi."""
        return Token(TokenType.EOF, "", 0, 0)

    def advance(self) -> Token:
        """Ambil token sekarang lalu geser posisi ke token berikutnya."""
        return Token(TokenType.EOF, "", 0, 0)

    def match(self, type: TokenType, value: str | None = None) -> bool:
        """Cek apakah token sekarang bertipe `type` (dan bernilai `value` jika diisi)."""
        return False

    def expect(self, type: TokenType, value: str | None = None) -> Token:
        """Ambil token yang wajib bertipe `type`, raise QuerySyntaxError jika tidak sesuai."""
        return Token(type, value or "", 0, 0)

    def is_at_end(self) -> bool:
        """True jika semua token sudah dibaca (token sekarang EOF)."""
        return True


class Lexer:
    def tokenize(self, sql: str) -> TokenStream:
        """Memecah string SQL menjadi aliran token"""
        return TokenStream()
