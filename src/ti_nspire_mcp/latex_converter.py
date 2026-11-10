"""
Conversor de expresiones matemáticas en LaTeX a etiquetas XML matemáticas nativas
de Texas Instruments TI-Nspire (<expr>...</expr>).
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

# Mapa de símbolos LaTeX a caracteres Unicode matemáticos nativos de TI-Nspire OS
_LATEX_SYMBOL_MAP: dict[str, str] = {
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\xi": "ξ",
    r"\pi": "π",
    r"\rho": "ρ",
    r"\sigma": "σ",
    r"\tau": "τ",
    r"\phi": "φ",
    r"\varphi": "φ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Delta": "Δ",
    r"\Gamma": "Γ",
    r"\Lambda": "Λ",
    r"\Omega": "Ω",
    r"\Phi": "Φ",
    r"\Pi": "Π",
    r"\Sigma": "Σ",
    r"\Theta": "Θ",
    r"\infty": "∞",
    r"\pm": "±",
    r"\mp": "∓",
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\approx": "≈",
    r"\cdot": "·",
    r"\times": "·",
    r"\div": "/",
    r"\partial": "∂",
    r"\to": "→",
    r"\rightarrow": "→",
    r"\Rightarrow": "⇒",
}

_LATEX_FUNC_MAP: dict[str, str] = {
    r"\sin": "sin",
    r"\cos": "cos",
    r"\tan": "tan",
    r"\cot": "cot",
    r"\sec": "sec",
    r"\csc": "csc",
    r"\arcsin": "arcsin",
    r"\arccos": "arccos",
    r"\arctan": "arctan",
    r"\sinh": "sinh",
    r"\cosh": "cosh",
    r"\tanh": "tanh",
    r"\ln": "ln",
    r"\log": "log",
    r"\exp": "exp",
}


def _extract_braced(text: str, start_idx: int) -> tuple[str, int] | None:
    """Extrae el contenido balanceado entre llaves { ... } comenzando en start_idx."""
    if start_idx >= len(text) or text[start_idx] != "{":
        return None
    depth = 0
    content_chars: list[str] = []
    for i in range(start_idx, len(text)):
        ch = text[i]
        if ch == "{":
            if depth > 0:
                content_chars.append(ch)
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(content_chars), i + 1
            content_chars.append(ch)
        else:
            content_chars.append(ch)
    return None


def _replace_fracs(expr: str) -> str:
    """Reemplaza recursivamente \\frac{num}{den} por ((num)/(den))."""
    while True:
        idx = expr.find(r"\frac")
        if idx == -1:
            break
        pos = idx + len(r"\frac")
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
        first = _extract_braced(expr, pos)
        if first is None:
            break
        num_str, next_pos = first
        while next_pos < len(expr) and expr[next_pos].isspace():
            next_pos += 1
        second = _extract_braced(expr, next_pos)
        if second is None:
            break
        den_str, end_pos = second
        num_conv = _replace_fracs(num_str)
        den_conv = _replace_fracs(den_str)
        expr = f"{expr[:idx]}(({num_conv})/({den_conv})){expr[end_pos:]}"
    return expr


def _replace_sqrts(expr: str) -> str:
    """Reemplaza \\sqrt[n]{x} por root(x,n) y \\sqrt{x} por √(x) soportando llaves anidadas."""
    while True:
        idx = expr.find(r"\sqrt")
        if idx == -1:
            break
        pos = idx + len(r"\sqrt")
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
        degree: str | None = None
        if pos < len(expr) and expr[pos] == "[":
            end_bracket = expr.find("]", pos + 1)
            if end_bracket == -1:
                break
            degree = expr[pos + 1 : end_bracket].strip()
            pos = end_bracket + 1
            while pos < len(expr) and expr[pos].isspace():
                pos += 1
        braced = _extract_braced(expr, pos)
        if braced is None:
            break
        inner, end_pos = braced
        inner_conv = _replace_sqrts(inner)
        if degree is not None:
            expr = f"{expr[:idx]}root({inner_conv},{degree}){expr[end_pos:]}"
        else:
            expr = f"{expr[:idx]}√({inner_conv}){expr[end_pos:]}"
    return expr


def _replace_matrices(expr: str) -> str:
    """Convierte entornos \\begin{pmatrix/bmatrix/matrix} a sintaxis matricial TI-Nspire [a,b;c,d]."""
    pattern = re.compile(
        r"\\begin\{(?:p|b|v|V)?matrix\}(.*?)\\end\{(?:p|b|v|V)?matrix\}",
        re.DOTALL,
    )

    def _conv_matrix(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        rows_raw = [r.strip() for r in body.split(r"\\") if r.strip()]
        rows_fmt: list[str] = []
        for r in rows_raw:
            cols = [c.strip() for c in r.split("&")]
            rows_fmt.append(",".join(cols))
        return "[" + ";".join(rows_fmt) + "]"

    return pattern.sub(_conv_matrix, expr)


def _replace_integrals_and_sums(expr: str) -> str:
    """
    Convierte integrales definidas \\int_{a}^{b} f(x) dx -> ∫(f(x),x,a,b)
    y sumatorias \\sum_{i=a}^{b} body -> ∑(body,i,a,b).
    """
    int_pattern = re.compile(r"\\int_\{([^{}]+)\}\^\{([^{}]+)\}\s*(.+?)\s*d([a-zA-Z])")
    expr = int_pattern.sub(r"∫(\3,\4,\1,\2)", expr)

    indef_int_pattern = re.compile(r"\\int\s+(.+?)\s*d([a-zA-Z])")
    expr = indef_int_pattern.sub(r"∫(\1,\2)", expr)

    sum_pattern = re.compile(r"\\sum_\{([a-zA-Z])\s*=\s*([^{}]+)\}\^\{([^{}]+)\}\s*([^\s]+)")
    expr = sum_pattern.sub(r"∑(\4,\1,\2,\3)", expr)
    return expr


def latex_to_nspire_expr(latex: str) -> str:
    """
    Transforma una cadena en formato LaTeX a la notación de expresión matemática
    interna evaluable por TI-Nspire OS.
    """
    s = latex.strip()
    # Eliminar delimitadores $ o $$ o \[ \]
    if s.startswith("$$") and s.endswith("$$") and len(s) >= 4:
        s = s[2:-2].strip()
    elif s.startswith("$") and s.endswith("$") and len(s) >= 2:
        s = s[1:-1].strip()
    elif s.startswith(r"\[") and s.endswith(r"\]"):
        s = s[2:-2].strip()
    elif s.startswith(r"\(") and s.endswith(r"\)"):
        s = s[2:-2].strip()

    # Eliminar \left y \right
    s = s.replace(r"\left", "").replace(r"\right", "")

    # Matrices, integrales, sumatorias, fracciones y raíces
    s = _replace_matrices(s)
    s = _replace_integrals_and_sums(s)
    s = _replace_fracs(s)
    s = _replace_sqrts(s)

    # Reemplazar funciones trigonométricas y logarítmicas
    for latex_fn, ti_fn in sorted(_LATEX_FUNC_MAP.items(), key=lambda x: -len(x[0])):
        s = re.sub(re.escape(latex_fn) + r"\b", ti_fn, s)

    # Reemplazar símbolos griegos y operadores
    for latex_sym, ti_sym in sorted(_LATEX_SYMBOL_MAP.items(), key=lambda x: -len(x[0])):
        s = s.replace(latex_sym, ti_sym)

    # Convertir potencias ^{...} -> ^(...)
    s = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", s)
    # Convertir subíndices _{...} -> _...
    s = re.sub(r"_\{([^{}]+)\}", r"_\1", s)

    # Limpiar llaves remanentes simples
    s = s.replace("{", "(").replace("}", ")")
    # Normalizar espacios múltiples
    s = re.sub(r"\s+", " ", s).strip()
    return s


def latex_to_nspire_xml(latex: str, evaluate: bool = False) -> str:
    """
    Convierte una expresión en LaTeX a una etiqueta XML matemática nativa de TI-Nspire (<expr>).
    """
    nspire_expr = latex_to_nspire_expr(latex)
    eval_attr = "1" if evaluate else "0"
    return f'<expr eval="{eval_attr}" latex="{escape(latex.strip())}">{escape(nspire_expr)}</expr>'
