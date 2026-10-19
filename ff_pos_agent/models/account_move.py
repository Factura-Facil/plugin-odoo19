"""La factura de contabilidad como fuente de un documento FE (ver ff.fe.source).

Cubre lo que el POS no ve: las facturas emitidas desde Contabilidad/Ventas y sus
notas de credito. Comparte con `pos.order` toda la aritmetica fiscal (mixin
`ff.fe.source`) y solo aporta de donde salen los datos: las lineas de la
factura, los cobros registrados y la configuracion de la compania.

Doble emision: una factura creada desde el POS NO se emite aqui. El pedido POS
ya la emitio con sus pagos reales; emitirla otra vez crearia un segundo
documento fiscal (el agente no deduplica) con otro CUFE para la misma venta.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Lo que se factura electronicamente: ventas y sus notas de credito.
FF_MOVE_TYPES = ("out_invoice", "out_refund")


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "ff.fe.source"]

    ff_desde_pos = fields.Boolean(
        "Factura de una orden POS", compute="_compute_ff_desde_pos",
        help="La orden POS ya emitió la factura electrónica de esta venta.")

    def _compute_ff_desde_pos(self):
        # Una sola consulta para todo el lote (no una por factura).
        desde_pos = set()
        ventas = self.filtered(lambda m: m.move_type in FF_MOVE_TYPES)
        if ventas:
            ordenes = self.env["pos.order"].sudo().search_read(
                [("account_move", "in", ventas.ids)], ["account_move"])
            desde_pos = {o["account_move"][0] for o in ordenes if o["account_move"]}
        for move in self:
            move.ff_desde_pos = move.id in desde_pos

    ff_fe_aplica = fields.Boolean(
        "La FE aplica a esta factura", compute="_compute_ff_fe_aplica",
        help="Factura de cliente (o nota de crédito) de una compañía con Factura "
             "Fácil activo. Marca si la cabecera debe mostrar el estado FE.")

    @api.depends("move_type", "company_id.ff_inv_enabled", "company_id.ff_inv_base_url",
                 "ff_desde_pos")
    def _compute_ff_fe_aplica(self):
        for move in self:
            move.ff_fe_aplica = bool(
                move.move_type in FF_MOVE_TYPES
                and move.company_id._ff_inv_configured()
                and not move.ff_desde_pos
            )

    @api.depends("state", "move_type", "fe_document_id",
                 "company_id.ff_inv_enabled", "company_id.ff_inv_base_url")
    def _compute_ff_puede_firmar(self):
        return super()._compute_ff_puede_firmar()

    # ------------------------------------------------------------------
    # Emision automatica al confirmar
    # ------------------------------------------------------------------
    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted:
            if not move.company_id.ff_inv_auto_emit or not move._ff_emitible():
                continue
            # Sin HTTP dentro del _post: si la emision fallara (agente caido),
            # una excepcion aqui desharia la confirmacion de la factura. Se crea
            # el documento y el cron lo emite en su propia transaccion, igual
            # que hace el POS al cobrar.
            try:
                move._ff_ensure_document()
                self.env["ff.fe.document"]._ff_schedule_cron()
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "[ff_pos_agent] no se pudo preparar la FE de la factura %s (id=%s)",
                    move.name, move.id)
        return posted

    def button_draft(self):
        # Un documento ya timbrado ante la DGI no se "despublica": el asiento
        # puede volver a borrador, pero el documento fiscal sigue existiendo y
        # se anula con una nota de credito. Se avisa en el log de la factura.
        for move in self:
            if move.fe_estado in ("timbrada", "autorizada"):
                move.message_post(body=_(
                    "Esta factura ya fue emitida ante la DGI (CUFE %s). Volver a "
                    "borrador no anula el documento fiscal: para anularlo hay que "
                    "emitir una nota de crédito.", move.fe_cufe or ""))
        return super().button_draft()

    # ------------------------------------------------------------------
    # Contrato de fuente (ver ff.fe.source)
    # ------------------------------------------------------------------
    def _ff_doc_vals(self):
        vals = super()._ff_doc_vals()
        vals["account_move_id"] = self.id
        return vals

    def _ff_emitible(self):
        """Solo ventas confirmadas, con la compania configurada, y nunca una
        factura que ya emitio el POS."""
        self.ensure_one()
        return bool(
            self.move_type in FF_MOVE_TYPES
            and self.state == "posted"
            and self.company_id._ff_inv_configured()
            and not self.ff_desde_pos
        )

    def _ff_no_emitible_msg(self):
        if self.ff_desde_pos:
            return _("Esta factura viene de una orden del POS, que ya emitió su factura "
                     "electrónica. Firma desde la orden POS para no duplicar el documento.")
        if self.move_type not in FF_MOVE_TYPES:
            return _("Solo las facturas de cliente y sus notas de crédito se emiten "
                     "ante la DGI.")
        if self.state != "posted":
            return _("La factura debe estar confirmada para poder firmarla.")
        return _("La compañía no tiene Factura Fácil activo para facturas: revisa "
                 "Ajustes → Factura electrónica Panamá.")

    def _ff_agent_config(self):
        """Conexion con el agente para ff.fe.document: la de la compania."""
        c = self.company_id.sudo()
        return {
            "enabled": bool(c.ff_inv_enabled),
            "mode": c.ff_inv_mode or "agent",
            "base_url": c.ff_inv_base_url,
            "company_uuid": c.ff_inv_company_uuid,
            "api_key": c.ff_inv_api_key,
            "env_name": c._ff_inv_env_name(),
            "print_cafe": bool(c.ff_inv_print_cafe),
        }

    def _ff_header_id(self):
        name = self.name
        return name if (name and name != "/") else ("INV-%s" % self.id)

    def _ff_is_refund(self):
        """La nota de credito se emite como NTC. Odoo guarda sus lineas en
        positivo (al reves que el POS); el sentido lo da el tipo de documento."""
        self.ensure_one()
        return self.move_type == "out_refund"

    def _ff_referred(self):
        """Documento afectado por la NC: CUFE de 66 chars (preferido) o el
        numero fiscal de 10 digitos, mas su fecha de emision (YYYY-MM-DD)."""
        self.ensure_one()
        origin = self.reversed_entry_id
        if not origin:
            return None
        fd_number = origin.fe_cufe or origin.fe_nro_dgi
        if not fd_number:
            return None
        date = origin.invoice_date or origin.date or fields.Date.context_today(self)
        return {"fd_number": fd_number, "fd_date": date.isoformat()}

    def _ff_partner(self):
        return self.partner_id

    def _ff_lines(self):
        """Lineas de producto: sin secciones, notas ni lineas a cero."""
        return self.invoice_line_ids.filtered(
            lambda l: l.display_type not in ("line_section", "line_note")
            and l.quantity != 0)

    def _ff_line_qty(self, line):
        return line.quantity

    def _ff_line_base(self, line):
        # Tambien en una nota de credito las lineas vienen con su signo natural
        # (venta positiva, descuento negativo): el sentido lo da el tipo NTC.
        return line.price_subtotal

    def _ff_line_tax(self, line):
        return line.price_total - line.price_subtotal

    def _ff_line_label(self, line):
        return line.name or (line.product_id.display_name if line.product_id else "")

    def _ff_total(self):
        return self.amount_total

    def _ff_date(self):
        return self.invoice_date or self.date

    def _ff_pos_code(self):
        return self.company_id.ff_inv_pos_code

    def _ff_branch_code(self):
        return self.company_id.ff_inv_branch_code

    def _ff_environment(self):
        return self.company_id.ff_inv_environment

    def _ff_gns_default(self):
        return self.company_id.ff_inv_codigo_gns

    def _ff_credit_installments(self):
        return self.company_id.ff_inv_credit_installments

    def _ff_info_tag(self):
        return "[D]OdooINV=%s[/D]" % (self.name or "")

    def _ff_manager_group(self):
        return "account.group_account_manager"

    # ------------------------------------------------------------------
    # Cobros -> formas de pago DGI
    # ------------------------------------------------------------------
    def _ff_payments_widget(self):
        """Los cobros conciliados contra la factura, como los muestra Odoo."""
        self.ensure_one()
        widget = self.invoice_payments_widget or {}
        if isinstance(widget, dict):
            return widget.get("content") or []
        return []

    def _ff_payment_journal(self, pmt):
        """El diario que registro el cobro. El widget no lo trae directo: da el
        pago (account_payment_id) o su asiento (move_id), y de ahi sale."""
        if pmt.get("account_payment_id"):
            pago = self.env["account.payment"].browse(pmt["account_payment_id"])
            if pago.exists():
                return pago.journal_id
        if pmt.get("move_id"):
            asiento = self.env["account.move"].browse(pmt["move_id"])
            if asiento.exists():
                return asiento.journal_id
        return self.env["account.journal"]

    def _ff_payment_type_of(self, pmt):
        """Forma de pago DGI del cobro, por el diario que lo registro."""
        journal = self._ff_payment_journal(pmt)
        if journal:
            return journal.ff_payment_type or journal._ff_guess_payment_type(), journal.name
        return "99", pmt.get("payment_method_name") or "Otro"

    def _ff_build_payments(self):
        """Lo cobrado va con su forma de pago; lo que queda pendiente es
        credito (01), que es lo normal en una factura a plazo."""
        payments = []
        for pmt in self._ff_payments_widget():
            amount = round(abs(pmt.get("amount") or 0.0), 2)
            if not amount:
                continue
            ptype, nombre = self._ff_payment_type_of(pmt)
            payment = {"type": ptype, "amount": amount}
            if ptype == "99":
                # 'Otro' exige una descripcion de 10 a 100 caracteres.
                desc = (nombre or "Otro").strip()
                payment["description"] = (desc if len(desc) >= 10
                                          else "%s (cobro factura)" % desc)[:100]
            payments.append(payment)
        pendiente = round(abs(self.amount_residual or 0.0), 2)
        if pendiente:
            payments.append({"type": "01", "amount": pendiente})
        if not payments:
            payments = [{"type": "01", "amount": round(abs(self.amount_total), 2)}]
        return payments

    def _ff_payment_names(self):
        """Para el CAFE: los cobros con el nombre de su diario. En una factura
        no hay vuelto."""
        pagos = []
        for pmt in self._ff_payments_widget():
            amount = abs(pmt.get("amount") or 0.0)
            if amount:
                journal = self._ff_payment_journal(pmt)
                pagos.append((journal.name or pmt.get("payment_method_name") or "", amount))
        pendiente = abs(self.amount_residual or 0.0)
        if round(pendiente, 2):
            pagos.append((_("Crédito"), pendiente))
        return pagos, 0.0
