from odoo import api, fields, models

# Formas de pago de la DGI (schema DocumentPayment del agente).
FF_PAYMENT_TYPES = [
    ("01", "Crédito (a plazo)"),
    ("02", "Efectivo"),
    ("03", "Tarjeta de crédito"),
    ("04", "Tarjeta de débito"),
    ("05", "Tarjeta de fidelidad"),
    ("06", "Vale"),
    ("07", "Tarjeta de regalo"),
    ("08", "Transferencia o depósito"),
    ("09", "Cheque"),
    ("10", "Punto de venta"),
    ("99", "Otro"),
]


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    ff_payment_type = fields.Selection(
        FF_PAYMENT_TYPES, string="Forma de pago DGI",
        compute="_compute_ff_payment_type", store=True, readonly=False, precompute=True,
        help="Cómo se informa este método a la DGI en la factura electrónica. Se "
             "propone a partir del tipo y del nombre del método; revísalo. 'Otro' "
             "viaja con el nombre del método como descripción.")

    @api.depends("type", "name")
    def _compute_ff_payment_type(self):
        for method in self:
            # Solo se propone si esta vacio: nunca se pisa lo que eligio el usuario.
            method.ff_payment_type = method.ff_payment_type or method._ff_guess_payment_type()

    def _is_write_forbidden(self, fields):
        # Odoo bloquea editar un metodo con sesiones abiertas (salvo el orden).
        # La forma de pago DGI no toca la contabilidad: se debe poder corregir
        # sin cerrar las cajas.
        return super()._is_write_forbidden(set(fields) - {"ff_payment_type"})

    def _ff_guess_payment_type(self):
        self.ensure_one()
        if self.type == "pay_later":
            return "01"  # cuenta de cliente: venta a credito
        if self.type == "cash" or self.is_cash_count:
            return "02"
        name = (self.name or "").lower()
        # Antes que "card"/"tarjeta": "gift card", "tarjeta de regalo".
        if any(k in name for k in ("regalo", "gift")):
            return "07"
        if any(k in name for k in ("debito", "débito", "debit", "clave")):
            return "04"
        if any(k in name for k in ("tarjeta", "card", "credit", "crédito", "visa", "master", "amex")):
            return "03"
        if any(k in name for k in ("transfer", "ach", "yappy", "nequi", "deposit", "depósito")):
            return "08"
        if any(k in name for k in ("cheque", "check")):
            return "09"
        # Un nombre que no dice nada no se hace pasar por efectivo.
        return "99"
