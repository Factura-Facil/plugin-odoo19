{
    "name": "Factura Electrónica Panamá (Factura Fácil) - POS",
    "summary": "Factura electrónica DGI Panamá en el Punto de Venta y en las "
               "facturas de Contabilidad con Factura Fácil (Agente Offline o API "
               "en línea): CUFE y QR oficial en el recibo y en el CAFE",
    # La descripcion de la tienda de apps esta en static/description/index.html.
    "description": "Al cobrar una venta en el POS, Odoo (servidor) la emite en el "
                   "Agente Offline de Factura Fácil y guarda el CUFE y el QR "
                   "oficial de la DGI para imprimir el CAFE en el recibo. Una "
                   "caida del agente no bloquea la venta: queda pendiente y se "
                   "emite sola. Ver README.md.",
    "author": "vDevCodes",
    "website": "https://vdevcodes.com",
    "maintainer": "vDevCodes",
    "category": "Point of Sale",
    "version": "19.0.6.2.0",
    "license": "OPL-1",
    "depends": ["point_of_sale", "account", "uom"],
    "data": [
        "security/ir.model.access.csv",
        "security/ff_security.xml",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
        "views/pos_order_views.xml",
        "views/account_move_views.xml",
        "views/res_partner_views.xml",
        "views/product_template_views.xml",
        "views/fe_catalogos_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "assets": {
        "point_of_sale._assets_pos": [
            "ff_pos_agent/static/src/**/*",
        ],
        # El dialogo de cliente del POS es el formulario de backend, pero el POS
        # sirve su propio bundle: el widget del validador tiene que estar en los
        # dos (paginas distintas, no hay doble registro).
        "web.assets_backend": [
            "ff_pos_agent/static/src/backend/**/*",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
