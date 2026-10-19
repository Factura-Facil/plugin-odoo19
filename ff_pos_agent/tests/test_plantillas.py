"""Las plantillas Owl del módulo usan operadores que Owl sabe traducir.

Owl no compila expresiones Python. Su tabla de traducción (WORD_REPLACEMENT en
`owl.js`) cubre `and`, `or`, `gt`, `gte`, `lt` y `lte` — y nada más. Un `not`
escrito por costumbre se compila como un identificador y **la plantilla entera
deja de compilar**, lo que en el POS significa pantalla en blanco.

Pasó en producción con el recibo: la venta se cobraba y se timbraba, y el POS
moría al pintar la pantalla siguiente. Este test existe para que no vuelva.
"""
import re
from pathlib import Path

from odoo.tests import TransactionCase, tagged

# Lo único que Owl traduce (owl.js: WORD_REPLACEMENT).
OPERADORES_VALIDOS = {"and", "or", "gt", "gte", "lt", "lte"}
# Palabras de Python que se cuelan por costumbre y Owl NO entiende.
OPERADORES_PROHIBIDOS = {"not", "is"}

ATRIBUTOS = re.compile(r'\bt-(?:if|elif|att-[\w-]+|esc|out|foreach|value|on-\w+)="([^"]*)"')
PALABRA = re.compile(r"\b([a-z]+)\b")


@tagged("post_install", "-at_install")
class TestPlantillasOwl(TransactionCase):

    def test_sin_operadores_que_owl_no_traduce(self):
        raiz = Path(__file__).resolve().parent.parent / "static" / "src"
        problemas = []
        for xml in sorted(raiz.rglob("*.xml")):
            for n, linea in enumerate(xml.read_text(encoding="utf-8").splitlines(), 1):
                for expresion in ATRIBUTOS.findall(linea):
                    for palabra in PALABRA.findall(expresion):
                        if palabra in OPERADORES_PROHIBIDOS:
                            problemas.append(
                                "%s:%s  usa '%s' — Owl solo traduce %s. "
                                "Con '%s' la plantilla entera no compila."
                                % (xml.name, n, palabra,
                                   ", ".join(sorted(OPERADORES_VALIDOS)), palabra))
        self.assertFalse(problemas, "\n" + "\n".join(problemas))
