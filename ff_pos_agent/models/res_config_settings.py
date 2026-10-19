from odoo import api, fields, models

from .pos_config import FF_DEFAULT_URL


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_ff_agent_enabled = fields.Boolean(
        related="pos_config_id.ff_agent_enabled", readonly=False)
    pos_ff_mode = fields.Selection(
        related="pos_config_id.ff_mode", readonly=False)
    pos_ff_base_url = fields.Char(
        related="pos_config_id.ff_base_url", readonly=False)
    pos_ff_company_uuid = fields.Char(
        related="pos_config_id.ff_company_uuid", readonly=False,
        groups="point_of_sale.group_pos_manager")
    pos_ff_api_key = fields.Char(
        related="pos_config_id.ff_api_key", readonly=False,
        groups="point_of_sale.group_pos_manager")
    pos_ff_pos_code = fields.Char(
        related="pos_config_id.ff_pos_code", readonly=False)
    pos_ff_branch_code = fields.Char(
        related="pos_config_id.ff_branch_code", readonly=False)
    pos_ff_environment = fields.Selection(
        related="pos_config_id.ff_environment", readonly=False)
    pos_ff_print_cafe = fields.Boolean(
        related="pos_config_id.ff_print_cafe", readonly=False)
    pos_ff_credit_installments = fields.Boolean(
        related="pos_config_id.ff_credit_installments", readonly=False)
    pos_ff_codigo_gns = fields.Char(
        related="pos_config_id.ff_codigo_gns", readonly=False)

    # --- Facturas de backend: la config vive en la compania ---
    ff_inv_enabled = fields.Boolean(
        related="company_id.ff_inv_enabled", readonly=False)
    ff_inv_auto_emit = fields.Boolean(
        related="company_id.ff_inv_auto_emit", readonly=False)
    ff_inv_mode = fields.Selection(
        related="company_id.ff_inv_mode", string="Modo de emisión (facturas)", readonly=False)
    ff_inv_base_url = fields.Char(
        related="company_id.ff_inv_base_url", string="URL de Factura Fácil (facturas)", readonly=False)
    ff_inv_company_uuid = fields.Char(
        related="company_id.ff_inv_company_uuid", string="Company UUID (facturas)", readonly=False,
        groups="account.group_account_invoice")
    ff_inv_api_key = fields.Char(
        related="company_id.ff_inv_api_key", string="API Key (facturas)", readonly=False,
        groups="account.group_account_invoice")
    ff_inv_pos_code = fields.Char(
        related="company_id.ff_inv_pos_code", string="Código punto de facturación (facturas)", readonly=False)
    ff_inv_branch_code = fields.Char(
        related="company_id.ff_inv_branch_code", string="Código sucursal (facturas)", readonly=False)
    ff_inv_environment = fields.Selection(
        related="company_id.ff_inv_environment", string="Ambiente FE (facturas)", readonly=False)
    ff_inv_print_cafe = fields.Boolean(
        related="company_id.ff_inv_print_cafe", string="Imprimir CAFE en el agente (facturas)", readonly=False)
    ff_inv_credit_installments = fields.Boolean(
        related="company_id.ff_inv_credit_installments", string="Informar cuotas de crédito (facturas)", readonly=False)
    ff_inv_codigo_gns = fields.Char(
        related="company_id.ff_inv_codigo_gns", string="Código GNS por defecto (facturas)", readonly=False)

    @api.onchange("ff_inv_mode")
    def _onchange_ff_inv_mode(self):
        if not self.ff_inv_base_url or self.ff_inv_base_url in FF_DEFAULT_URL.values():
            self.ff_inv_base_url = FF_DEFAULT_URL[self.ff_inv_mode or "agent"]

    def action_ff_inv_test_connection(self):
        return self.company_id.action_ff_inv_test_connection()

    @api.onchange("pos_ff_mode")
    def _onchange_pos_ff_mode(self):
        if not self.pos_ff_base_url or self.pos_ff_base_url in FF_DEFAULT_URL.values():
            self.pos_ff_base_url = FF_DEFAULT_URL[self.pos_ff_mode or "agent"]

    def action_ff_test_connection(self):
        return self.pos_config_id.action_ff_test_connection()
