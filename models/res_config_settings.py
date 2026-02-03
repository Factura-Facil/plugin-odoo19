import json
import logging

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

API_URL_QA = "https://backend-qa-api.facturafacil.com.pa/api/pac/reception_fe/detailed/"
API_URL_PROD = "https://api.facturafacil.com.pa/api/pac/reception_fe/detailed/"


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ff_api_url = fields.Char(
        string="URL del API",
        config_parameter='facturafacil.api_url',
        default=API_URL_QA,
    )
    ff_environment = fields.Selection(
        [('qa', 'QA (Pruebas)'), ('production', 'Producción')],
        string="Ambiente",
        config_parameter='facturafacil.environment',
        default='qa',
    )
    ff_company_uuid = fields.Char(
        string="X-FF-Company",
        config_parameter='facturafacil.company_uuid',
    )
    ff_branch_uuid = fields.Char(
        string="X-FF-Branch",
        config_parameter='facturafacil.branch_uuid',
    )
    ff_api_key = fields.Char(
        string="X-FF-API-Key",
        config_parameter='facturafacil.api_key',
    )
    ff_sucursal = fields.Char(
        string="Código de Sucursal",
        config_parameter='facturafacil.sucursal',
        default='0000',
        help="Código de sucursal (4 caracteres alfanuméricos)",
    )
    ff_caja = fields.Char(
        string="Número de Caja",
        config_parameter='facturafacil.caja',
        default='001',
        help="Número de caja/punto de venta (3 dígitos)",
    )
    ff_max_retries = fields.Integer(
        string="Máximo de Reintentos",
        config_parameter='facturafacil.max_retries',
        default=3,
    )
    ff_auto_send = fields.Boolean(
        string="Envío Automático",
        config_parameter='facturafacil.auto_send',
        default=False,
        help="Enviar automáticamente al confirmar la factura",
    )

    @api.onchange('ff_environment')
    def _onchange_ff_environment(self):
        if self.ff_environment == 'qa':
            self.ff_api_url = API_URL_QA
        elif self.ff_environment == 'production':
            self.ff_api_url = API_URL_PROD

    def action_ff_test_connection(self):
        """Test connection to Factura Fácil API."""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('facturafacil.api_url', '')
        company_uuid = ICP.get_param('facturafacil.company_uuid', '')
        branch_uuid = ICP.get_param('facturafacil.branch_uuid', '')
        api_key = ICP.get_param('facturafacil.api_key', '')

        if not all([api_url, company_uuid, branch_uuid, api_key]):
            raise UserError("Configure todas las credenciales antes de probar la conexión.")

        headers = {
            'Content-Type': 'application/json',
            'X-FF-Company': company_uuid,
            'X-FF-Branch': branch_uuid,
            'X-FF-API-Key': api_key,
        }

        try:
            # Use GET on the base endpoint to test credentials
            test_url = api_url.replace('/detailed/', '/')
            response = requests.get(test_url, headers=headers, timeout=10)

            if response.status_code in (200, 201, 404, 405):
                message = "Conexión exitosa con el API de Factura Fácil."
            elif response.status_code == 401:
                raise UserError("Credenciales inválidas. Verifique X-FF-Company, X-FF-Branch y X-FF-API-Key.")
            elif response.status_code == 403:
                raise UserError("Sin permisos para acceder al API.")
            else:
                message = f"Conexión establecida (HTTP {response.status_code})."

        except requests.Timeout:
            raise UserError("Timeout: El servidor no respondió en 10 segundos.")
        except requests.ConnectionError:
            raise UserError("No se pudo conectar al servidor. Verifique la URL del API.")
        except requests.RequestException as e:
            raise UserError(f"Error de conexión: {str(e)}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Factura Fácil',
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }
