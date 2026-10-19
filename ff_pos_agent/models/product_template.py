from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ff_gns_code = fields.Char(
        string="Código GNS (CPBS)", size=4,
        help="Código del artículo en la Codificación Panameña de Bienes y "
             "Servicios. Obligatorio en facturas a Gobierno (receptor tipo 03); "
             "si se deja vacío se usa el código por defecto del punto de venta.")
