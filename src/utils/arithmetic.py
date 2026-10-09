"""仅解释数值算术 AST，不执行 Python 表达式。"""

import ast
import math
import operator
from typing import Union

Number = Union[int, float]
_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate_arithmetic(expression: str) -> Number:
    """受限解析数字与算术运算，拒绝名称、调用及过量计算。"""
    if len(expression) > 256:
        raise ValueError("算术表达式过长")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("算术表达式过于复杂")

    def calculate(node: ast.AST) -> Number:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            result = _UNARY[type(node.op)](calculate(node.operand))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            left, right = calculate(node.left), calculate(node.right)
            # 限制幂运算和中间结果，避免配置构造巨整数耗尽资源。
            if isinstance(node.op, ast.Pow) and abs(right) > 32:
                raise ValueError("幂指数超出限制")
            result = _BINARY[type(node.op)](left, right)
        else:
            raise ValueError("只允许数字和算术运算")
        if type(result) not in (int, float) or abs(result) > 10 ** 15 or not math.isfinite(result):
            raise ValueError("算术结果超出限制")
        return result

    return calculate(tree.body)
