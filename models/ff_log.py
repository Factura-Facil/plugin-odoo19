from odoo import fields, models


class FFLog(models.Model):
    _name = 'ff.log'
    _description = 'Factura Fácil - Log de Envíos'
    _order = 'send_date desc'

    move_id = fields.Many2one(
        'account.move',
        string="Factura",
        required=True,
        ondelete='cascade',
        index=True,
    )
    request_json = fields.Text(string="Request JSON")
    response_json = fields.Text(string="Response JSON")
    http_code = fields.Integer(string="Código HTTP")
    ff_state = fields.Selection(
        [
            ('success', 'Éxito'),
            ('error', 'Error'),
            ('timeout', 'Timeout'),
        ],
        string="Estado",
    )
    cufe = fields.Char(string="CUFE")
    error_message = fields.Text(string="Mensaje de Error")
    response_time_ms = fields.Integer(string="Tiempo de Respuesta (ms)")
    send_date = fields.Datetime(
        string="Fecha de Envío",
        default=fields.Datetime.now,
    )
    company_id = fields.Many2one(
        related='move_id.company_id',
        store=True,
    )
