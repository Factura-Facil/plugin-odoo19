{
    'name': 'Factura Fácil Panamá Connector',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'Integración con el API de Factura Fácil Panamá para facturación electrónica',
    'description': """
Factura Fácil Panamá - Conector de Facturación Electrónica
==========================================================

Módulo para enviar facturas y notas de crédito al API de Factura Fácil Panamá.

Características:
- Envío automático o manual de facturas electrónicas
- Soporte para notas de crédito con referencia a factura original
- Historial completo de envíos con request/response
- Reintentos automáticos con exponential backoff
- Configuración de credenciales por compañía
- Mapeo de impuestos ITBMS (Panamá)
    """,
    'author': 'Grupo ARI',
    'website': 'https://grupo-ari.net',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/res_partner_views.xml',
        'views/account_tax_views.xml',
        'views/ff_log_views.xml',
        'data/cron.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
