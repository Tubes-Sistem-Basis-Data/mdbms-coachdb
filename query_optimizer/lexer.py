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
    """Aliran token hasil Lexer yang dibaca oleh parser (peek, advance, dll.)"""
    pass


class Lexer:
    def tokenize(self, sql: str) -> TokenStream:
        """Memecah string SQL menjadi aliran token"""
        raise NotImplementedError