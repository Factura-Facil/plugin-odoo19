import json
import logging
import time
from datetime import datetime

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MIN_INTERVAL = 1.5  # seconds between API requests
TIMEOUT = 30  # HTTP timeout in seconds


class FacturaFacilAPI:
    """Helper class for Factura Fácil API communication."""

    _last_request_time = 0.0

    def __init__(self, api_url, company_uuid, branch_uuid, api_key):
        self.api_url = api_url
        self.headers = {
            'Content-Type': 'application/json',
            'X-FF-Company': company_uuid,
            'X-FF-Branch': branch_uuid,
            'X-FF-API-Key': api_key,
        }

    def _wait_rate_limit(self):
        if FacturaFacilAPI._last_request_time > 0:
            elapsed = time.time() - FacturaFacilAPI._last_request_time
            if elapsed < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - elapsed)

    def send(self, payload):
        """Send payload to the API. Returns (success, http_code, response_data, response_time_ms, raw_response)."""
        self._wait_rate_limit()
        start_time = time.time()
        FacturaFacilAPI._last_request_time = start_time

        try:
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self.headers,
                timeout=TIMEOUT,
            )
            response_time_ms = int((time.time() - start_time) * 1000)
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                data = {}

            return True, response.status_code, data, response_time_ms, response.text

        except requests.Timeout:
            response_time_ms = int((time.time() - start_time) * 1000)
            return False, 0, {}, response_time_ms, "Timeout"

        except requests.ConnectionError:
            response_time_ms = int((time.time() - start_time) * 1000)
            return False, 0, {}, response_time_ms, "ConnectionError"

        except requests.RequestException as e:
            response_time_ms = int((time.time() - start_time) * 1000)
            return False, 0, {}, response_time_ms, str(e)


class AccountMove(models.Model):
    _inherit = 'account.move'

    ff_state = fields.Selection(
        [
            ('draft', 'No Enviado'),
            ('sending', 'Enviando'),
            ('sent', 'Enviado'),
            ('validated', 'Validado (CUFE)'),
            ('rejected', 'Rechazado'),
            ('error', 'Error'),
        ],
        string="Estado FE",
        default='draft',
        copy=False,
        tracking=True,
    )
    ff_cufe = fields.Char(
        string="CUFE",
        copy=False,
        help="Código Único de Factura Electrónica",
    )
    ff_last_error = fields.Text(
        string="Último Error FE",
        copy=False,
    )
    ff_send_date = fields.Datetime(
        string="Fecha de Envío FE",
        copy=False,
    )
    ff_retry_count = fields.Integer(
        string="Reintentos FE",
        default=0,
        copy=False,
    )
    ff_log_ids = fields.One2many(
        'ff.log',
        'move_id',
        string="Historial de Envíos FE",
    )
    ff_log_count = fields.Integer(
        compute='_compute_ff_log_count',
        string="Envíos",
    )

    @api.depends('ff_log_ids')
    def _compute_ff_log_count(self):
        for move in self:
            move.ff_log_count = len(move.ff_log_ids)

    def _get_ff_api_client(self):
        """Build and return a FacturaFacilAPI instance from config."""
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = ICP.get_param('facturafacil.api_url', '')
        company_uuid = ICP.get_param('facturafacil.company_uuid', '')
        branch_uuid = ICP.get_param('facturafacil.branch_uuid', '')
        api_key = ICP.get_param('facturafacil.api_key', '')

        if not all([api_url, company_uuid, branch_uuid, api_key]):
            raise UserError(
                "Configure las credenciales de Factura Fácil en "
                "Contabilidad > Configuración > Factura Fácil Panamá."
            )

        return FacturaFacilAPI(api_url, company_uuid, branch_uuid, api_key)

    def _prepare_ff_payload(self):
        """Build the JSON payload for a regular invoice."""
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        environment = ICP.get_param('facturafacil.environment', 'qa')
        environment_code = '2' if environment == 'qa' else '1'

        partner = self.partner_id
        items = []
        # In Odoo 19, display_type='product' for real lines; exclude sections/notes
        product_lines = self.invoice_line_ids.filtered(
            lambda l: l.display_type not in ('line_section', 'line_note')
        )
        for idx, line in enumerate(product_lines, start=1):
            tax_entries = []
            for tax in line.tax_ids:
                itbms_code = tax.ff_itbms_code or '00'
                # Calculate tax amount for this line
                base_amount = line.quantity * line.price_unit
                discount_amount = base_amount * (line.discount / 100.0) if line.discount else 0.0
                taxable_base = base_amount - discount_amount

                itbms_rates = {'00': 0.0, '01': 0.07, '02': 0.10, '03': 0.15}
                rate = itbms_rates.get(itbms_code, 0.0)
                tax_amount = round(taxable_base * rate, 2)

                tax_entries.append({
                    'type': '01',
                    'amount': tax_amount,
                    'code': itbms_code,
                })

            if not tax_entries:
                tax_entries.append({'type': '01', 'amount': 0.0, 'code': '00'})

            # Calculate line discount as absolute amount
            line_discount = round(
                line.quantity * line.price_unit * (line.discount / 100.0), 2
            ) if line.discount else 0.0

            items.append({
                'line': idx,
                'price': round(line.price_unit, 2),
                'mu': 'und',
                'quantity': round(line.quantity, 2),
                'description': line.name or line.product_id.name or 'Producto',
                'taxes': tax_entries,
                'discount': line_discount,
            })

        payload = {
            'header': {
                'id': self.id,
                'environment': environment_code,
            },
            'document': {
                'fd_number': '0',
                'emission_date': self.invoice_date.strftime('%Y-%m-%d') if self.invoice_date else fields.Date.today().strftime('%Y-%m-%d'),
                'receptor': self._build_receptor(partner),
                'items': items,
                'payments': [{
                    'type': '02',
                    'description': 'Pago a crédito',
                    'amount': str(round(self.amount_total, 2)),
                }],
            },
        }
        return payload

    def _prepare_ff_payload_credit_note(self):
        """Build the JSON payload for a credit note."""
        self.ensure_one()
        payload = self._prepare_ff_payload()

        # Add reference to original invoice
        origin_move = self.reversed_entry_id
        if origin_move and origin_move.ff_cufe:
            payload['document']['referenced_documents'] = [{
                'date': origin_move.invoice_date.strftime('%Y-%m-%d') if origin_move.invoice_date else '',
                'cufe': origin_move.ff_cufe,
                'type': 'FE',
                'reason': self.ref or 'Nota de crédito',
            }]

        return payload

    def _build_receptor(self, partner):
        """Build the receptor dict for the API payload."""
        receptor = {
            'type': partner.ff_receptor_type or '02',
            'name': partner.name or '',
            'ruc': partner.vat or '',
            'dv': partner.ff_dv or '',
            'ruc_type': partner.ff_ruc_type or '1',
            'address': partner.street or '',
            'email': partner.email or '',
        }
        # Location required for Contribuyente (01) and Gobierno (03)
        if partner.ff_receptor_type in ('01', '03'):
            receptor['location'] = '8-8-1'
        return receptor

    def action_send_to_facturafacil(self):
        """Manual send button action."""
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                raise UserError("Solo se pueden enviar facturas de cliente y notas de crédito.")
            if move.state != 'posted':
                raise UserError("La factura debe estar confirmada para enviarla.")
            move._do_send_to_facturafacil()

    def action_retry_facturafacil(self):
        """Retry sending to Factura Fácil."""
        for move in self:
            move.ff_retry_count += 1
            move._do_send_to_facturafacil()

    def _do_send_to_facturafacil(self):
        """Core logic for sending to the API."""
        self.ensure_one()
        self.ff_state = 'sending'
        self.ff_send_date = fields.Datetime.now()

        api_client = self._get_ff_api_client()

        # Build payload based on move type
        if self.move_type == 'out_refund':
            payload = self._prepare_ff_payload_credit_note()
        else:
            payload = self._prepare_ff_payload()

        request_json = json.dumps(payload, ensure_ascii=False, indent=2)

        # Send to API
        connected, http_code, data, response_time_ms, raw_response = api_client.send(payload)

        # Create log entry
        log_vals = {
            'move_id': self.id,
            'request_json': request_json,
            'response_json': self._format_json_string(raw_response),
            'http_code': http_code,
            'response_time_ms': response_time_ms,
            'send_date': fields.Datetime.now(),
        }

        if not connected:
            # Connection error or timeout
            log_vals['ff_state'] = 'timeout' if 'Timeout' in raw_response else 'error'
            log_vals['error_message'] = raw_response
            self.ff_state = 'error'
            self.ff_last_error = raw_response
        elif 'rejected' in data:
            if data['rejected'] is False:
                # Success
                cufe = (
                    data.get('cufe', '') or
                    data.get('CUFE', '') or
                    (data.get('data', {}) or {}).get('cufe', '') or
                    (data.get('data', {}) or {}).get('CUFE', '')
                )
                # Extract messages
                messages = data.get('messages', [])
                api_message = self._extract_api_messages(messages)

                log_vals['ff_state'] = 'success'
                log_vals['cufe'] = cufe
                self.ff_state = 'validated'
                self.ff_cufe = cufe
                self.ff_last_error = False
            else:
                # Rejected
                messages = data.get('messages', [])
                api_message = self._extract_api_messages(messages)
                error_msg = api_message or "Factura rechazada por el API (rejected=true)"

                log_vals['ff_state'] = 'error'
                log_vals['error_message'] = error_msg
                self.ff_state = 'rejected'
                self.ff_last_error = error_msg
        else:
            # No 'rejected' field - fallback to HTTP code
            if http_code in (200, 201):
                cufe = (
                    data.get('cufe', '') or
                    data.get('CUFE', '') or
                    (data.get('data', {}) or {}).get('cufe', '') or
                    (data.get('data', {}) or {}).get('CUFE', '')
                )
                log_vals['ff_state'] = 'success'
                log_vals['cufe'] = cufe
                self.ff_state = 'validated' if cufe else 'sent'
                self.ff_cufe = cufe
                self.ff_last_error = False
            else:
                error_msg = (
                    data.get('message', '') or
                    data.get('error', '') or
                    data.get('detail', '') or
                    f"Error HTTP {http_code}"
                )
                log_vals['ff_state'] = 'error'
                log_vals['error_message'] = error_msg
                self.ff_state = 'error'
                self.ff_last_error = error_msg

        self.env['ff.log'].sudo().create(log_vals)

    @staticmethod
    def _format_json_string(raw):
        """Try to pretty-print a JSON string for log readability."""
        try:
            parsed = json.loads(raw)
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, ValueError, TypeError):
            return raw

    def _extract_api_messages(self, messages):
        """Extract readable message string from API messages list."""
        if not messages or not isinstance(messages, list):
            return ''
        parts = []
        for msg in messages:
            if isinstance(msg, dict):
                text = msg.get('message', '')
                code = msg.get('code', '')
                if text:
                    parts.append(f"[{code}] {text}" if code else text)
        return ' | '.join(parts)

    def action_post(self):
        """Override to auto-send to Factura Fácil after posting."""
        res = super().action_post()

        ICP = self.env['ir.config_parameter'].sudo()
        auto_send = ICP.get_param('facturafacil.auto_send', 'False')

        if auto_send and auto_send.lower() in ('true', '1'):
            for move in self:
                if move.move_type in ('out_invoice', 'out_refund') and move.ff_state == 'draft':
                    try:
                        move._do_send_to_facturafacil()
                    except Exception as e:
                        _logger.error("Auto-send to Factura Fácil failed for move %s: %s", move.id, str(e))
                        move.ff_state = 'error'
                        move.ff_last_error = str(e)

        return res

    def _cron_retry_failed_sends(self):
        """Cron job: retry failed sends with exponential backoff."""
        ICP = self.env['ir.config_parameter'].sudo()
        max_retries = int(ICP.get_param('facturafacil.max_retries', '3'))

        moves = self.search([
            ('ff_state', 'in', ('error', 'sending')),
            ('ff_retry_count', '<', max_retries),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
        ])

        for move in moves:
            # Exponential backoff: skip if not enough time has passed
            if move.ff_send_date:
                minutes_since_last = (fields.Datetime.now() - move.ff_send_date).total_seconds() / 60
                backoff_minutes = 2 ** move.ff_retry_count  # 1, 2, 4, 8, 16...
                if minutes_since_last < backoff_minutes:
                    continue

            try:
                move.ff_retry_count += 1
                move._do_send_to_facturafacil()
                self.env.cr.commit()
            except Exception as e:
                _logger.error("Cron retry failed for move %s: %s", move.id, str(e))
                move.ff_state = 'error'
                move.ff_last_error = str(e)
                self.env.cr.commit()
