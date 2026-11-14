from dataclasses import dataclass
from enum import Enum, auto
from typing import NoReturn

from common.errors import QuerySyntaxError


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


# tambah keywords disini
KEYWORDS: frozenset[str] = frozenset({
    "SELECT", "FROM", "WHERE", "ORDER", "BY", "ASC", "DESC", "JOIN", "ON", "NATURAL", "AND", "UPDATE", "SET", "INSERT",
    "INTO", "VALUES", "DELETE", "BEGIN", "TRANSACTION", "COMMIT", "ABORT", "CREATE", "INDEX", "USING", "BTREE",
    "HASH", "EXPLAIN", "LIMIT", "AS", "TABLE", "DROP", "CASCADE", "RESTRICT", "PRIMARY", "KEY", "FOREIGN",
    "REFERENCES",
})

# tanda baca satu karakter dan tipe tokennya
PUNCT: dict[str, TokenType] = {
    ",": TokenType.COMMA,
    ".": TokenType.DOT,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    ";": TokenType.SEMICOLON,
}


@dataclass
class Token:
    type: TokenType
    value: str | int | float
    line: int
    col: int
    pos: int = 0


class TokenStream:
    """Aliran token hasil Lexer yang dibaca parser satu per satu."""

    def __init__(self, tokens: list[Token] | None = None) -> None:
        self._tokens: list[Token] = tokens or []
        self._pos: int = 0

    def peek(self, offset: int = 0) -> Token:
        """Lihat token di posisi sekarang + offset tanpa menggeser posisi."""
        idx = self._pos + offset
        if len(self._tokens) == 0:
            return Token(TokenType.EOF, "", 0, 0)
        elif idx >= len(self._tokens):
            return self._tokens[len(self._tokens) - 1]
        return self._tokens[idx]

    def advance(self) -> Token:
        """Ambil token sekarang lalu geser posisi ke token berikutnya."""
        tok = self.peek()
        if tok.type != TokenType.EOF:
            self._pos += 1
        return tok

    def match(self, type: TokenType, value: str | None = None) -> bool:
        """Cek apakah token sekarang bertipe `type` (dan bernilai `value` jika diisi)."""
        tok = self.peek()
        if tok.type != type:
            return False
        elif value is None:
            return True
        if type == TokenType.KEYWORD:
            if tok.value == value.upper():
                return True
        elif tok.value == value:
            return True
        return False

    def expect(self, type: TokenType, value: str | None = None) -> Token:
        """Ambil token yang wajib bertipe `type`, raise QuerySyntaxError jika tidak sesuai."""
        if self.match(type, value):
            return self.advance()
        self.error(f"diharapkan {value or type.name}")

    def error(self, msg: str, tok: Token | None = None) -> NoReturn:
        """Lempar QuerySyntaxError dengan format pesan seragam. Dipakai semua parser QO."""
        if tok is None:
            tok = self.peek()
        if tok.type == TokenType.EOF:
            teks = "akhir query"
        else:
            teks = str(tok.value)
        pesan = f"Kesalahan sintaks baris {tok.line} kolom {tok.col} dekat '{teks}': {msg}"
        raise QuerySyntaxError(pesan, position=tok.pos, token=teks)

    def is_at_end(self) -> bool:
        """True jika semua token sudah dibaca (token sekarang EOF)."""
        if self.peek().type == TokenType.EOF:
            return True
        return False


class Lexer:
    def tokenize(self, sql: str) -> TokenStream:
        """Memecah string SQL menjadi aliran token"""
        self._sql = sql
        self._i = 0
        self._line = 1
        self._col = 1
        tokens: list[Token] = []
        while self._i < len(self._sql):
            char = self._peek_char()
            if char.isspace():
                self._bump()
            elif char.isalpha() or char == "_":
                tokens.append(self._read_word())
            elif char.isdigit():
                tokens.append(self._read_number())
            elif char in "'\"":
                tokens.append(self._read_string())
            elif char in "<>=!+-*/":
                tokens.append(self._read_operator())
            elif char in PUNCT:
                tokens.append(self._read_punct())
            else:
                self._error("karakter tidak dikenal", self._line, self._col, self._i, char)
        tokens.append(Token(TokenType.EOF, "", self._line, self._col, pos=self._i))
        return TokenStream(tokens)

    def _bump(self) -> str:
        """ Ambil karakter sekarang terus maju satu, dan update line/col"""
        char = self._sql[self._i]
        self._i += 1
        if char == "\n":
            self._line +=1
            self._col = 1
        else:
            self._col +=1
        return char

    def _peek_char(self, offset: int = 0) -> str:
        """Lihat karakter di posisi sekarang + offset tanpa maju. Kosong jika lewat akhir sql."""
        idx = self._i + offset
        if idx >= len(self._sql):
            return ""
        return self._sql[idx]

    def _error(self, msg: str, line: int, col: int, pos: int, teks: str) -> NoReturn:
        """Lempar QuerySyntaxError dari lexer dengan format yang sama dengan TokenStream.error."""
        pesan = f"Kesalahan sintaks baris {line} kolom {col} dekat '{teks}': {msg}"
        raise QuerySyntaxError(pesan, position=pos, token=teks)

    def _read_punct(self) -> Token:
        """Baca satu tanda baca: , . ( ) ;"""
        start_i, start_line, start_col = self._i, self._line, self._col
        c = self._bump()
        return Token(PUNCT[c], c, start_line, start_col, pos=start_i)

    def _read_word(self) -> Token:
        """Baca identifier atau keyword: huruf/_ diikuti huruf, angka, atau _."""
        start_i, start_line, start_col = self._i, self._line, self._col
        while self._peek_char().isalnum() or self._peek_char() == "_":
            self._bump()
        word = self._sql[start_i:self._i]
        if word.upper() in KEYWORDS:
            return Token(TokenType.KEYWORD, word.upper(), start_line, start_col, pos=start_i)
        return Token(TokenType.IDENT, word, start_line, start_col, pos=start_i)

    def _read_operator(self) -> Token:
        """Baca operator. Dua karakter dicek lebih dulu (maximal munch)."""
        start_i, start_line, start_col = self._i, self._line, self._col
        dua = self._peek_char(0) + self._peek_char(1)
        if dua in (">=", "<=", "<>", "!="):
            self._bump()
            self._bump()
            value = "<>" if dua == "!=" else dua
            return Token(TokenType.OP, value, start_line, start_col, pos=start_i)
        elif self._peek_char() in "=<>+-*/":
            c = self._bump()
            return Token(TokenType.OP, c, start_line, start_col, pos=start_i)
        else:
            self._error("karakter tidak dikenal", start_line, start_col, start_i, self._peek_char())

    def _read_number(self) -> Token:
        """Baca INTEGER atau FLOAT. '3.' tanpa digit setelahnya dibaca INTEGER 3 lalu DOT."""
        start_i, start_line, start_col = self._i, self._line, self._col
        is_float = False
        while self._peek_char().isdigit():
            self._bump()
        if self._peek_char() == "." and self._peek_char(1).isdigit():
            self._bump()
            while self._peek_char().isdigit():
                self._bump()
            is_float = True
        # angka yang langsung diikuti huruf, mis. '3abc' atau '3.5x'
        if self._peek_char().isalpha() or self._peek_char() == "_":
            teks_salah = self._sql[start_i:self._i + 1]
            self._error("angka tidak valid", start_line, start_col, start_i, teks_salah)
        teks = self._sql[start_i:self._i]
        if is_float:
            return Token(TokenType.FLOAT, float(teks), start_line, start_col, pos=start_i)
        return Token(TokenType.INTEGER, int(teks), start_line, start_col, pos=start_i)

    def _read_string(self) -> Token:
        """Baca literal teks berkutip tunggal atau ganda, value tanpa kutip. """
        start_i, start_line, start_col = self._i, self._line, self._col
        # skip kutip pembuka, simpan jenisnya untuk mencari penutup yang sama
        kutip = self._bump()
        while self._peek_char() != kutip:
            if self._peek_char() == "":
                self._error("string tidak ditutup", start_line, start_col, start_i, kutip)
            self._bump()
        isi = self._sql[start_i + 1:self._i]
        # skip kutip penutup
        self._bump()
        return Token(TokenType.STRING, isi, start_line, start_col, pos=start_i)
    