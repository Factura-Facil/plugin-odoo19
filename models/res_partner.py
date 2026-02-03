from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ff_receptor_type = fields.Selection(
        [
            ('01', 'Contribuyente'),
            ('02', 'Consumidor Final'),
            ('03', 'Gobierno'),
            ('04', 'Extranjero'),
        ],
        string="Tipo de Receptor (FE)",
        default='02',
        help="Tipo de receptor para facturación electrónica de Panamá",
    )
    ff_ruc_type = fields.Selection(
        [
            ('1', 'Natural'),
            ('2', 'Jurídico'),
        ],
        string="Tipo de RUC",
        help="Tipo de RUC del contribuyente",
    )
    ff_dv = fields.Char(
        string="Dígito Verificador",
        size=2,
        help="Dígito verificador del RUC (requerido para Contribuyente y Gobierno)",
    )
