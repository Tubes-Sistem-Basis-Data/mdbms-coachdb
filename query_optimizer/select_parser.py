from common.plan import ParsedQuery, PlanNode, QueryKind
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from query_optimizer.lexer import TokenStream


class SelectParser:
    def parse_select(self, tokens: "TokenStream") -> ParsedQuery:
        """Mengenali SELECT ... FROM ... [WHERE] [ORDER BY] [LIMIT]"""
        return ParsedQuery(sql="", kind=QueryKind.SELECT)

    def _parse_from(self, tokens: "TokenStream") -> PlanNode:
        """Mengenali klausa FROM, termasuk JOIN, NATURAL JOIN, dan cartesian"""
        return PlanNode()

    def _parse_order_by(self, tokens: "TokenStream") -> PlanNode:
        """Mengenali klausa ORDER BY"""
        return PlanNode()

    def _parse_limit_alias(self, tokens: "TokenStream") -> PlanNode:
        """Mengenali klausa LIMIT dan alias"""
        return PlanNode()
