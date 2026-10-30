"""Query Optimizer – Group 3 (Odin).

Mengubah teks SQL menjadi plan yang tervalidasi dan teroptimasi.
Pipeline: lexer → parser → binder → rewrite heuristik → enumerasi plan → biaya → plan termurah.
"""

from query_optimizer.optimizer import QueryOptimizerImpl  # noqa: F401

