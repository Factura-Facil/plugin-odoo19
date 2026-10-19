from odoo import fields, models


class UomUom(models.Model):
    _inherit = "uom.uom"

    ff_dgi_code = fields.Char(
        "Código DGI", size=10,
        help="Unidad de medida en el catálogo de la DGI para la factura electrónica "
             "(p. ej. 'und', 'kg'). Vacío = 'und'. OJO: el nombre de la unidad en Odoo "
             "no sirve (está traducido y la DGI lo rechaza con el error 2002).")
