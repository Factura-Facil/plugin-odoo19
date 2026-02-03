from odoo import fields, models


class AccountTax(models.Model):
    _inherit = 'account.tax'

    ff_itbms_code = fields.Selection(
        [
            ('00', 'Exento (0%)'),
            ('01', 'ITBMS 7%'),
            ('02', 'ITBMS 10%'),
            ('03', 'ITBMS 15%'),
        ],
        string="Código ITBMS",
        help="Código de impuesto ITBMS para Factura Fácil Panamá",
    )
