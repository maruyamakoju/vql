"""VQL Parser — tokenizer + recursive-descent parser.

Produces an AST (abstract syntax tree) from a VQL query string.
The AST is consumed by VQLExecutor without re-running any model.

Grammar (simplified BNF)
------------------------
    query      := SELECT var FROM vir_expr WHERE predicates RETURN returns
    vir_expr   := VIR "(" STRING ")"
    predicates := predicate ( AND predicate )*
    predicate  := enters_pred | exits_pred | stays_pred | duration_pred
                | type_pred | sequence_pred | time_of_day_pred
    enters_pred  := ENTERS "(" var "," zone_expr ( "," opts )* ")"
    exits_pred   := EXITS  "(" var "," zone_expr ( "," opts )* ")"
    stays_pred   := STAYS  "(" var "," zone_expr ")" cmp_op duration_lit
    duration_pred:= DURATION "(" var "," zone_expr ")" cmp_op duration_lit
    type_pred    := TYPE   "(" var "," STRING ")"
    sequence_pred:= SEQUENCE "(" predicate ("," predicate)+ ")"
    zone_expr    := zone "(" STRING ")" | STRING
    duration_lit := NUMBER ( "min" | "s" | "sec" | "h" )
    cmp_op       := "<" | "<=" | ">" | ">=" | "==" | "!="
    opts         := time_range_opt | count_opt
    time_range_opt := time_range "(" kwarg* ")"
    returns    := return_item ("," return_item)*
    return_item:= IDENT | evidence_frames_expr
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


# ── Token types ─────────────────────────────────────────────────────────────

class TT(Enum):
    # Keywords
    SELECT = auto(); FROM = auto(); WHERE = auto()
    AND = auto(); OR = auto(); RETURN = auto()
    WITHIN = auto(); NOT = auto(); IN = auto()
    # Built-in functions / predicates
    VIR_FN = auto(); ZONE = auto()
    ENTERS = auto(); EXITS = auto(); STAYS = auto()
    DURATION = auto(); TYPE_FN = auto(); SEQUENCE = auto()
    PROXIMITY = auto(); TIME_RANGE = auto(); TIME_OF_DAY = auto()
    COUNT = auto(); SPEED = auto()
    EVIDENCE_FRAMES = auto()
    # Literals
    STRING = auto(); NUMBER = auto()
    DURATION_LIT = auto()   # e.g. "5min", "30s", "1h"
    IDENT = auto()
    # Punctuation
    LPAREN = auto(); RPAREN = auto(); COMMA = auto()
    EQ = auto(); NEQ = auto(); LT = auto(); LE = auto()
    GT = auto(); GE = auto()
    EOF = auto()


_KEYWORDS: dict[str, TT] = {
    "SELECT": TT.SELECT, "FROM": TT.FROM, "WHERE": TT.WHERE,
    "AND": TT.AND, "OR": TT.OR, "RETURN": TT.RETURN,
    "WITHIN": TT.WITHIN, "NOT": TT.NOT, "IN": TT.IN,
}
_FUNCTIONS: dict[str, TT] = {
    "VIR": TT.VIR_FN, "zone": TT.ZONE,
    "ENTERS": TT.ENTERS, "EXITS": TT.EXITS,
    "STAYS": TT.STAYS, "DURATION": TT.DURATION,
    "TYPE": TT.TYPE_FN, "SEQUENCE": TT.SEQUENCE,
    "PROXIMITY": TT.PROXIMITY,
    "TIME_RANGE": TT.TIME_RANGE, "time_range": TT.TIME_RANGE,  # both cases
    "TIME_OF_DAY": TT.TIME_OF_DAY, "time_of_day": TT.TIME_OF_DAY,
    "COUNT": TT.COUNT,
    "SPEED": TT.SPEED, "evidence_frames": TT.EVIDENCE_FRAMES,
}
_DUR_UNITS = {"min", "s", "sec", "h"}
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)(min|sec|s|h)")


@dataclass
class Token:
    type: TT
    value: str
    pos: int = 0


class VQLSyntaxError(Exception):
    def __init__(self, msg: str, pos: int = -1):
        super().__init__(msg)
        self.pos = pos


# ── Tokenizer ───────────────────────────────────────────────────────────────

class _Tokenizer:
    def __init__(self, src: str):
        self._src = src
        self._pos = 0
        self._tokens: list[Token] = []

    def tokenize(self) -> list[Token]:
        while self._pos < len(self._src):
            self._skip_ws()
            if self._pos >= len(self._src):
                break
            c = self._src[self._pos]
            if c in ('"', "'"):
                self._string()
            elif c.isdigit():
                self._number()
            elif c.isalpha() or c == "_" or ord(c) > 127:
                self._ident()
            elif c == "(":
                self._emit(TT.LPAREN, "("); self._pos += 1
            elif c == ")":
                self._emit(TT.RPAREN, ")"); self._pos += 1
            elif c == ",":
                self._emit(TT.COMMA, ","); self._pos += 1
            elif c == "<":
                if self._src[self._pos:self._pos+2] == "<=":
                    self._emit(TT.LE, "<="); self._pos += 2
                else:
                    self._emit(TT.LT, "<"); self._pos += 1
            elif c == ">":
                if self._src[self._pos:self._pos+2] == ">=":
                    self._emit(TT.GE, ">="); self._pos += 2
                else:
                    self._emit(TT.GT, ">"); self._pos += 1
            elif c == "=" and self._src[self._pos:self._pos+2] == "==":
                self._emit(TT.EQ, "=="); self._pos += 2
            elif c == "=":
                self._emit(TT.EQ, "="); self._pos += 1
            elif c == "!" and self._src[self._pos:self._pos+2] == "!=":
                self._emit(TT.NEQ, "!="); self._pos += 2
            elif c == "-" and self._src[self._pos:self._pos+2] == "--":
                # line comment
                while self._pos < len(self._src) and self._src[self._pos] != "\n":
                    self._pos += 1
            else:
                self._pos += 1   # skip unknown char
        self._emit(TT.EOF, "")
        return self._tokens

    def _emit(self, tt: TT, val: str):
        self._tokens.append(Token(tt, val, self._pos))

    def _skip_ws(self):
        while self._pos < len(self._src) and self._src[self._pos] in " \t\n\r":
            self._pos += 1

    def _string(self):
        q = self._src[self._pos]
        start = self._pos
        self._pos += 1
        buf: list[str] = []
        while self._pos < len(self._src) and self._src[self._pos] != q:
            buf.append(self._src[self._pos])
            self._pos += 1
        self._pos += 1   # closing quote
        self._tokens.append(Token(TT.STRING, "".join(buf), start))

    def _number(self):
        start = self._pos
        while self._pos < len(self._src) and (self._src[self._pos].isdigit() or self._src[self._pos] == "."):
            self._pos += 1
        num = self._src[start:self._pos]
        # check for duration suffix immediately following the number
        m = _DUR_RE.match(self._src[start:])
        if m:
            self._tokens.append(Token(TT.DURATION_LIT, m.group(0), start))
            self._pos = start + len(m.group(0))
        else:
            self._tokens.append(Token(TT.NUMBER, num, start))

    def _ident(self):
        start = self._pos
        while self._pos < len(self._src) and (
            self._src[self._pos].isalnum()
            or self._src[self._pos] in "_-"
            or ord(self._src[self._pos]) > 127
        ):
            self._pos += 1
        word = self._src[start:self._pos]
        upper = word.upper()
        if upper in _KEYWORDS:
            self._tokens.append(Token(_KEYWORDS[upper], word, start))
        elif word in _FUNCTIONS:
            self._tokens.append(Token(_FUNCTIONS[word], word, start))
        else:
            # Could be duration like "5min" caught by _number; if not, it's an ident
            self._tokens.append(Token(TT.IDENT, word, start))


# ── AST node types ──────────────────────────────────────────────────────────

@dataclass
class DurationLit:
    """Parsed duration value, e.g. '5min' → seconds=300."""
    raw: str
    seconds: float

    @staticmethod
    def parse(s: str) -> "DurationLit":
        m = _DUR_RE.match(s)
        if not m:
            raise VQLSyntaxError(f"Invalid duration: {s!r}")
        n, unit = float(m.group(1)), m.group(2)
        mult = {"h": 3600, "min": 60, "s": 1, "sec": 1}[unit]
        return DurationLit(raw=s, seconds=n * mult)


@dataclass
class TimeRange:
    from_t: Optional[str] = None   # "HH:MM:SS"
    to_t:   Optional[str] = None

    def from_sec(self) -> Optional[float]:
        return _hms_to_sec(self.from_t) if self.from_t else None

    def to_sec(self) -> Optional[float]:
        return _hms_to_sec(self.to_t) if self.to_t else None


def _hms_to_sec(s: str) -> float:
    """Convert HH:MM:SS or HH:MM to seconds."""
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    return float(s)


@dataclass
class ZoneExpr:
    zone_id: str


@dataclass
class EntersPredicate:
    var: str
    zone: ZoneExpr
    time_range: Optional[TimeRange] = None
    count_gte: Optional[int] = None


@dataclass
class ExitsPredicate:
    var: str
    zone: ZoneExpr
    time_range: Optional[TimeRange] = None


@dataclass
class StaysPredicate:
    var: str
    zone: ZoneExpr
    op: str          # ">" | ">=" | "<" | "<="
    duration: DurationLit


@dataclass
class DurationPredicate:
    var: str
    zone: ZoneExpr
    op: str
    duration: DurationLit


@dataclass
class TypePredicate:
    var: str
    entity_type: str


@dataclass
class SequencePredicate:
    predicates: list[Any]


@dataclass
class TimeOfDayPredicate:
    var: str
    time_range: TimeRange


Predicate = (
    EntersPredicate | ExitsPredicate | StaysPredicate |
    DurationPredicate | TypePredicate | SequencePredicate |
    TimeOfDayPredicate
)


@dataclass
class ReturnItem:
    name: str
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class VQLQuery:
    """The complete parsed AST for a VQL query."""
    var: str
    source: str                  # VIR source path
    predicates: list[Any]
    returns: list[ReturnItem]
    raw: str = ""                # original query string


# ── Parser ──────────────────────────────────────────────────────────────────

class _Parser:
    def __init__(self, tokens: list[Token], src: str):
        self._tok = tokens
        self._pos = 0
        self._src = src

    # ── token navigation ────────────────────────────────────────────────────

    def _peek(self) -> Token:
        return self._tok[self._pos]

    def _advance(self) -> Token:
        t = self._tok[self._pos]
        self._pos += 1
        return t

    def _expect(self, tt: TT) -> Token:
        t = self._peek()
        if t.type != tt:
            raise VQLSyntaxError(
                f"Expected {tt.name} but got {t.type.name} ({t.value!r})", t.pos
            )
        return self._advance()

    def _match(self, *types: TT) -> bool:
        return self._peek().type in types

    # ── entry point ─────────────────────────────────────────────────────────

    def parse(self) -> VQLQuery:
        self._expect(TT.SELECT)
        var = self._expect(TT.IDENT).value
        self._expect(TT.FROM)
        source = self._parse_vir_expr()
        self._expect(TT.WHERE)
        predicates = self._parse_predicates()
        self._expect(TT.RETURN)
        returns = self._parse_returns()
        return VQLQuery(var=var, source=source, predicates=predicates, returns=returns)

    # ── VIR("path") ─────────────────────────────────────────────────────────

    def _parse_vir_expr(self) -> str:
        self._expect(TT.VIR_FN)
        self._expect(TT.LPAREN)
        path = self._expect(TT.STRING).value
        self._expect(TT.RPAREN)
        return path

    # ── predicate list ──────────────────────────────────────────────────────

    def _parse_predicates(self) -> list:
        preds = [self._parse_predicate()]
        while self._match(TT.AND, TT.OR):
            self._advance()   # consume AND/OR
            preds.append(self._parse_predicate())
        return preds

    def _parse_predicate(self) -> Any:
        t = self._peek()
        if t.type == TT.ENTERS:
            return self._parse_enters()
        if t.type == TT.EXITS:
            return self._parse_exits()
        if t.type == TT.STAYS:
            return self._parse_stays()
        if t.type == TT.DURATION:
            return self._parse_duration()
        if t.type == TT.TYPE_FN:
            return self._parse_type()
        if t.type == TT.SEQUENCE:
            return self._parse_sequence()
        if t.type == TT.TIME_OF_DAY:
            return self._parse_time_of_day()
        raise VQLSyntaxError(f"Unknown predicate: {t.value!r}", t.pos)

    # ── ENTERS(var, zone("Z"), opts?) ────────────────────────────────────────

    def _parse_enters(self) -> EntersPredicate:
        self._expect(TT.ENTERS)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.COMMA)
        zone = self._parse_zone_expr()
        tr = None
        count_gte = None
        while self._match(TT.COMMA):
            self._advance()
            kw_name = self._peek()
            if kw_name.type == TT.TIME_RANGE:
                tr = self._parse_time_range_fn()
            elif kw_name.type == TT.IDENT and kw_name.value == "count":
                self._advance()
                self._expect(TT.GE)
                count_gte = int(self._expect(TT.NUMBER).value)
            else:
                # skip unknown kwarg
                self._advance()
                if self._match(TT.EQ):
                    self._advance(); self._advance()
        self._expect(TT.RPAREN)
        return EntersPredicate(var=var, zone=zone, time_range=tr, count_gte=count_gte)

    # ── EXITS(var, zone("Z")) ────────────────────────────────────────────────

    def _parse_exits(self) -> ExitsPredicate:
        self._expect(TT.EXITS)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.COMMA)
        zone = self._parse_zone_expr()
        tr = None
        if self._match(TT.COMMA):
            self._advance()
            if self._peek().type == TT.TIME_RANGE:
                tr = self._parse_time_range_fn()
        self._expect(TT.RPAREN)
        return ExitsPredicate(var=var, zone=zone, time_range=tr)

    # ── STAYS(var, zone("Z")) > 5min ────────────────────────────────────────

    def _parse_stays(self) -> StaysPredicate:
        self._expect(TT.STAYS)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.COMMA)
        zone = self._parse_zone_expr()
        self._expect(TT.RPAREN)
        op = self._parse_cmp_op()
        dur = DurationLit.parse(self._expect(TT.DURATION_LIT).value)
        return StaysPredicate(var=var, zone=zone, op=op, duration=dur)

    # ── DURATION(var, zone("Z")) < 5min ─────────────────────────────────────

    def _parse_duration(self) -> DurationPredicate:
        self._expect(TT.DURATION)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.COMMA)
        zone = self._parse_zone_expr()
        self._expect(TT.RPAREN)
        op = self._parse_cmp_op()
        dur = DurationLit.parse(self._expect(TT.DURATION_LIT).value)
        return DurationPredicate(var=var, zone=zone, op=op, duration=dur)

    # ── TYPE(var, "person") ──────────────────────────────────────────────────

    def _parse_type(self) -> TypePredicate:
        self._expect(TT.TYPE_FN)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.COMMA)
        etype = self._expect(TT.STRING).value
        self._expect(TT.RPAREN)
        return TypePredicate(var=var, entity_type=etype)

    # ── SEQUENCE(pred, pred, …) ──────────────────────────────────────────────

    def _parse_sequence(self) -> SequencePredicate:
        self._expect(TT.SEQUENCE)
        self._expect(TT.LPAREN)
        preds = [self._parse_predicate()]
        while self._match(TT.COMMA):
            self._advance()
            if self._match(TT.RPAREN):
                break
            preds.append(self._parse_predicate())
        self._expect(TT.RPAREN)
        return SequencePredicate(predicates=preds)

    # ── TIME_OF_DAY(var) IN time_range(…) ───────────────────────────────────

    def _parse_time_of_day(self) -> TimeOfDayPredicate:
        self._expect(TT.TIME_OF_DAY)
        self._expect(TT.LPAREN)
        var = self._expect(TT.IDENT).value
        self._expect(TT.RPAREN)
        self._expect(TT.IN)
        tr = self._parse_time_range_fn()
        return TimeOfDayPredicate(var=var, time_range=tr)

    # ── zone("Z") helper ────────────────────────────────────────────────────

    def _parse_zone_expr(self) -> ZoneExpr:
        if self._match(TT.ZONE):
            self._advance()
            self._expect(TT.LPAREN)
            zid = self._expect(TT.STRING).value
            self._expect(TT.RPAREN)
            return ZoneExpr(zone_id=zid)
        if self._match(TT.STRING):
            return ZoneExpr(zone_id=self._advance().value)
        raise VQLSyntaxError(f"Expected zone expression, got {self._peek().value!r}")

    # ── time_range(from="…", to="…") ────────────────────────────────────────

    def _parse_time_range_fn(self) -> TimeRange:
        self._expect(TT.TIME_RANGE)
        self._expect(TT.LPAREN)
        tr = TimeRange()
        # Support both positional: time_range("HH:MM:SS", "HH:MM:SS")
        # and keyword: time_range(from="HH:MM:SS", to="HH:MM:SS")
        if self._match(TT.STRING):
            tr.from_t = self._advance().value
            if self._match(TT.COMMA):
                self._advance()
                if self._match(TT.STRING):
                    tr.to_t = self._advance().value
        else:
            while not self._match(TT.RPAREN, TT.EOF):
                kw = self._advance()      # "from" or "to"
                self._expect(TT.EQ)
                val = self._expect(TT.STRING).value
                if kw.value in ("from", "from_t"):
                    tr.from_t = val
                elif kw.value in ("to", "to_t"):
                    tr.to_t = val
                if self._match(TT.COMMA):
                    self._advance()
        self._expect(TT.RPAREN)
        return tr

    # ── comparison operator ──────────────────────────────────────────────────

    def _parse_cmp_op(self) -> str:
        t = self._advance()
        if t.type in (TT.LT, TT.LE, TT.GT, TT.GE, TT.EQ, TT.NEQ):
            return t.value
        raise VQLSyntaxError(f"Expected comparison operator, got {t.value!r}", t.pos)

    # ── RETURN items ────────────────────────────────────────────────────────

    def _parse_returns(self) -> list[ReturnItem]:
        items = [self._parse_return_item()]
        while self._match(TT.COMMA):
            self._advance()
            if self._match(TT.EOF):
                break
            items.append(self._parse_return_item())
        return items

    def _parse_return_item(self) -> ReturnItem:
        if self._match(TT.EVIDENCE_FRAMES):
            self._advance()
            kwargs = {}
            if self._match(TT.LPAREN):
                self._advance()
                while not self._match(TT.RPAREN, TT.EOF):
                    k = self._advance().value
                    self._expect(TT.EQ)
                    v = self._advance().value
                    kwargs[k] = v
                    if self._match(TT.COMMA):
                        self._advance()
                self._expect(TT.RPAREN)
            return ReturnItem(name="evidence_frames", kwargs=kwargs)
        name = self._advance().value
        return ReturnItem(name=name)


# ── Public API ───────────────────────────────────────────────────────────────

def parse_vql(src: str) -> VQLQuery:
    """Parse a VQL query string into an AST.

    Raises VQLSyntaxError on parse failure.

    Example
    -------
    >>> q = parse_vql('''
    ...     SELECT   person
    ...     FROM     VIR("entrance_cam_2h.mp4")
    ...     WHERE    ENTERS(person, zone("A区域"),
    ...                     time_range(from="14:00:00", to="15:00:00"))
    ...       AND    DURATION(person, zone("A区域")) < 5min
    ...     RETURN   track_id, enter_t, exit_t, duration, evidence_frames(n=2)
    ... ''')
    >>> print(q.var, q.source)
    person entrance_cam_2h.mp4
    """
    tokens = _Tokenizer(src).tokenize()
    query = _Parser(tokens, src).parse()
    query.raw = src
    return query
