"""Conexion con Factura Facil para las facturas de backend (`account.move`).

El POS lleva su configuracion en cada `pos.config` (una caja = un punto de
facturacion). Las facturas de contabilidad no salen de una caja, asi que su
emisor es la compania.

OJO con el codigo de punto de facturacion: Factura Facil numera los documentos
POR punto de venta. Si las facturas de backend usan el mismo codigo que una
caja, los correlativos de las dos se pisan y la DGI rechaza. Por eso el default
aqui es "002" y no "001": tiene que ser un punto propio, dado de alta en el
agente.
"""
from odoo import _, api, fields, models

from .ff_agent_probe import ff_probe_notification
from .pos_config import FF_DEFAULT_URL, FF_MODES

FF_ACCOUNTANT = "account.group_account_invoice"


class ResCompany(models.Model):
    _inherit = "res.company"

    ff_inv_enabled = fields.Boolean(
        string="Factura electrónica en facturas", default=False,
        help="Emite las facturas de contabilidad (fuera del POS) ante la DGI "
             "con Factura Fácil.")
    ff_inv_auto_emit = fields.Boolean(
        string="Emitir al confirmar", default=True,
        help="Al validar la factura se emite sola ante la DGI. Desactivado, "
             "queda esperando el botón 'Firmar / Emitir FE' de la factura.")
    ff_inv_mode = fields.Selection(
        FF_MODES, string="Modo de emisión", default="agent", required=True,
        help="Agente offline: la factura se firma en el agente de la empresa. "
             "API en línea: va directo a Factura Fácil por internet y el PDF "
             "oficial del CAFE queda adjunto a la factura.")
    ff_inv_base_url = fields.Char(
        string="URL de Factura Fácil", default=FF_DEFAULT_URL["agent"],
        help="Debe ser alcanzable desde el SERVIDOR de Odoo, no desde tu PC.")
    ff_inv_company_uuid = fields.Char(
        string="Company UUID (X-FF-Company)", groups=FF_ACCOUNTANT,
        help="UUID de la empresa en Factura Fácil.")
    ff_inv_api_key = fields.Char(
        string="API Key (X-FF-API-Key)", groups=FF_ACCOUNTANT,
        help="API key de 128 caracteres. Viaja como header desde el servidor.")
    ff_inv_pos_code = fields.Char(
        string="Código punto de facturación", default="002",
        help="header.pos - debe existir como PointOfSale en el agente y ser "
             "DISTINTO al de las cajas del POS: Factura Fácil numera por punto "
             "de venta y compartirlo choca los correlativos ante la DGI.")
    ff_inv_branch_code = fields.Char(
        string="Código sucursal", default="0000",
        help="header.branch.code - sucursal emisora configurada en el agente.")
    ff_inv_print_cafe = fields.Boolean(
        string="Imprimir CAFE en el agente", default=False,
        help="Tras timbrar, el agente imprime el CAFE en su propia impresora.")
    ff_inv_codigo_gns = fields.Char(
        string="Código GNS por defecto (CPBS)", size=4,
        help="Se usa cuando el receptor es Gobierno (tipo 03) y el artículo no "
             "tiene el suyo. La DGI rechaza la factura a gobierno sin gns.")
    ff_inv_credit_installments = fields.Boolean(
        string="Informar cuotas de crédito", default=False,
        help="Lo facturado a crédito se informa como venta a plazo con su cuota "
             "(payment_term 2/3 + installments). El agente probado (2026-09) "
             "falla con cuotas; activarlo solo cuando Factura Fácil lo corrija.")
    ff_inv_environment = fields.Selection(
        [("2", "Pruebas"), ("1", "Producción")],
        string="Ambiente FE", default="2",
        help="Debe coincidir con el modo del agente (IS_TEST). "
             "1=Producción (documento fiscal real), 2=Pruebas.")

    @api.onchange("ff_inv_mode")
    def _onchange_ff_inv_mode(self):
        # Solo si la URL es la de fabrica del otro modo: nunca pisa una propia.
        if not self.ff_inv_base_url or self.ff_inv_base_url in FF_DEFAULT_URL.values():
            self.ff_inv_base_url = FF_DEFAULT_URL[self.ff_inv_mode or "agent"]

    def action_ff_inv_test_connection(self):
        self.ensure_one()
        cfg = self.sudo()
        return ff_probe_notification(self.ff_inv_base_url, self.ff_inv_mode,
                                     cfg.ff_inv_company_uuid, cfg.ff_inv_api_key)

    def _ff_inv_env_name(self):
        """Nombre de ambiente para los endpoints REST del agente."""
        return "prod" if self.ff_inv_environment == "1" else "test"

    def _ff_inv_configured(self):
        self.ensure_one()
        return bool(self.ff_inv_enabled and self.ff_inv_base_url)
