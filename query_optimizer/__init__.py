"""Mock package untuk Query Optimizer.

Versi milestone 1 ini sengaja dibuat sangat sederhana: semua fungsi hanya
mengembalikan placeholder yang aman dan deterministik. Tujuannya adalah agar
komponen lain bisa mengintegrasikan API QO tanpa bergantung pada logika
optimasi asli.
"""

from query_optimizer.access_path_chooser import choose_access_path
from query_optimizer.binder import bind_query
from query_optimizer.cost_model import estimate_cost
from query_optimizer.expression_parser import ExpressionParser
from query_optimizer.genetic_join_optimizer import optimize_join_order
from query_optimizer.join_order_optimizer import order_joins
from query_optimizer.optimizer import MockQueryOptimizer
from query_optimizer.plan_printer import render_plan
from query_optimizer.planner import plan_query
from query_optimizer.projection_rewriter import rewrite_projection
from query_optimizer.selection_rewriter import rewrite_selection
from query_optimizer.statement_parser import StatementParser

__all__ = [
    "MockQueryOptimizer",
    "bind_query",
    "choose_access_path",
    "estimate_cost",
    "optimize_join_order",
    "order_joins",
    "ExpressionParser",
    "StatementParser",
    "plan_query",
    "render_plan",
    "rewrite_projection",
    "rewrite_selection",
]
