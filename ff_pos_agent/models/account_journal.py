from odoo import api, fields, models

from .pos_payment_method import FF_PAYMENT_TYPES


class AccountJournal(models.Model):
    _inherit = "account.journal"

    ff_payment_type = fields.Selection(
        FF_PAYMENT_TYPES, string="Forma de pago DGI",
        compute="_compute_ff_payment_type", store=True, readonly=False, precompute=True,
        help="Cómo se informa un cobro por este diario a la DGI en la factura "
             "electrónica. Se propone por el tipo y el nombre del diario; "
             "revísalo. 'Otro' viaja con el nombre del diario como descripción.")

    @api.depends("type", "name")
    def _compute_ff_payment_type(self):
        for journal in self:
            # Solo se propone si esta vacio: nunca se pisa lo que eligio el usuario.
            journal.ff_payment_type = journal.ff_payment_type or journal._ff_guess_payment_type()

    def _ff_guess_payment_type(self):
        self.ensure_one()
        name = (self.name or "").lower()
        if any(k in name for k in ("regalo", "gift")):
            return "07"
        if any(k in name for k in ("debito", "débito", "debit", "clave")):
            return "04"
        if any(k in name for k in ("tarjeta", "card", "credit", "crédito", "visa", "master", "amex")):
            return "03"
        if any(k in name for k in ("cheque", "check")):
            return "09"
        if any(k in name for k in ("transfer", "ach", "yappy", "nequi", "deposit", "depósito")):
            return "08"
        if self.type == "cash":
            return "02"
        if self.type == "bank":
            return "08"  # deposito / transferencia
        # Un diario que no dice nada no se hace pasar por efectivo.
        return "99"
