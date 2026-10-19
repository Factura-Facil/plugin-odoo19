import requests

from odoo import _, api, fields, models

# Credenciales del agente: solo las ve un responsable del POS (ni por RPC) y
# nunca viajan al navegador de la caja, aunque la abra un responsable. Quien
# habla con el agente es el servidor, que las lee sin pasar por esos permisos.
FF_SECRET_FIELDS = ("ff_company_uuid", "ff_api_key")
FF_MANAGER = "point_of_sale.group_pos_manager"

# Modo de emision: el mismo documento (POST /api/pac/reception_fe/detailed/)
# va al Agente Offline de la tienda o directo a la API en linea de Factura Facil.
FF_MODES = [
    ("agent", "Agente offline (desde el servidor)"),
    ("agent_local", "Agente en la caja (desde el navegador)"),
    ("online", "API en línea"),
]
FF_DEFAULT_URL = {
    "agent": "http://localhost:8080",
    # El agente vive en la propia caja: la URL la resuelve el navegador, no el
    # servidor, asi que 127.0.0.1 es correcto aqui (y solo aqui).
    "agent_local": "http://127.0.0.1:8080",
    # QA. La URL de produccion la entrega Factura Facil.
    "online": "https://backend-qa-api.facturafacil.com.pa",
}
# Modos en los que quien habla con el agente es el navegador de la caja.
FF_CLIENT_MODES = ("agent_local",)


class PosConfig(models.Model):
    _inherit = "pos.config"

    ff_agent_enabled = fields.Boolean(
        string="Factura electrónica (Factura Fácil)", default=True)
    ff_mode = fields.Selection(
        FF_MODES, string="Modo de emisión", default="agent", required=True,
        help="Agente offline: la factura se firma en el agente de la tienda, que "
             "sigue emitiendo sin internet e imprime el CAFE en su impresora. "
             "API en línea: la factura va directo a Factura Fácil por internet y se "
             "autoriza al momento; el CAFE se imprime con el recibo de Odoo y el PDF "
             "oficial queda adjunto a la orden. OJO: los dos modos comparten la "
             "numeración del punto de venta en Factura Fácil; no uses el mismo "
             "código POS en los dos modos a la vez.")
    ff_base_url = fields.Char(
        string="URL de Factura Fácil",
        default=FF_DEFAULT_URL["agent"],
        help="Agente offline: URL del agente, alcanzable desde el servidor de Odoo "
             "(p. ej. http://localhost:8080, o http://host.docker.internal:8080 "
             "desde un contenedor). API en línea: URL de la API de Factura Fácil "
             "(QA: https://backend-qa-api.facturafacil.com.pa).")
    ff_company_uuid = fields.Char(
        string="Company UUID (X-FF-Company)", groups=FF_MANAGER,
        help="UUID de la empresa en Factura Fácil.")
    ff_api_key = fields.Char(
        string="API Key (X-FF-API-Key)", groups=FF_MANAGER,
        help="API key de 128 caracteres. Se envía como header, del lado del "
             "servidor: no se carga en el navegador de la caja y solo la ve "
             "un responsable del Punto de Venta.")
    ff_pos_code = fields.Char(
        string="Código POS", default="001",
        help="header.pos - debe existir como PointOfSale en el agente.")
    ff_branch_code = fields.Char(
        string="Código sucursal", default="0000",
        help="header.branch.code - sucursal emisora configurada en el agente.")
    ff_print_cafe = fields.Boolean(
        string="Imprimir CAFE en el agente", default=False,
        help="Si está activo, tras timbrar se llama a printByCufe y el Agente "
             "de Factura Fácil imprime el CAFE en su propia impresora. "
             "Dejarlo desactivado si el CAFE se imprime desde el recibo del POS.")
    ff_codigo_gns = fields.Char(
        string="Código GNS por defecto (CPBS)", size=4,
        help="Código de la Codificación Panameña de Bienes y Servicios que se "
             "usa cuando el receptor es Gobierno (tipo 03) y el artículo no "
             "tiene el suyo propio. La DGI rechaza la factura a gobierno si el "
             "item no lleva gns. Máximo 4 caracteres.")
    ff_credit_installments = fields.Boolean(
        string="Informar cuotas de crédito", default=False,
        help="Lo cobrado a cuenta de cliente se informa como venta a plazo con su "
             "cuota (payment_term 2/3 + installments), como pide el contrato del "
             "agente. Desactivado: se informa como pago 'Crédito' al contado. El "
             "agente probado (2026-09) falla con cuotas (HTTP 500, NameError en "
             "generators.py:344); activarlo solo cuando Factura Fácil lo corrija.")
    ff_environment = fields.Selection(
        [("2", "Pruebas"), ("1", "Producción")],
        string="Ambiente FE", default="2",
        help="Debe coincidir con el modo del agente (IS_TEST). "
             "1=Producción (documento fiscal real), 2=Pruebas.")

    def _load_pos_data(self, data):
        # Por defecto los secretos NUNCA llegan al navegador de la caja: quien
        # habla con el agente es el servidor.
        #
        # La excepcion es el modo "agente en la caja": ahi el agente corre en
        # esa misma maquina y es el navegador quien lo llama, asi que necesita
        # las cabeceras. La clave no se "expone" a un tercero — ya vive en esa
        # caja, junto al agente que la usa — pero deja de ser un secreto de
        # servidor: quien tenga acceso a la caja puede leerla.
        res = super()._load_pos_data(data)
        for rec in res.get("data", []):
            local = rec.get("ff_mode") in FF_CLIENT_MODES
            for name in FF_SECRET_FIELDS:
                rec.pop(name, None)
            if local and rec.get("id"):
                # sudo: los campos llevan groups=responsable del POS, pero el
                # cajero necesita las credenciales para hablar con SU agente.
                cfg = self.browse(rec["id"]).sudo()
                for name in FF_SECRET_FIELDS:
                    rec[name] = cfg[name] or ""
        return res

    def _ff_is_client_mode(self):
        self.ensure_one()
        return self.ff_mode in FF_CLIENT_MODES

    @api.onchange("ff_mode")
    def _onchange_ff_mode(self):
        # Solo si la URL es la de fabrica del otro modo: nunca pisa una propia.
        if not self.ff_base_url or self.ff_base_url in FF_DEFAULT_URL.values():
            self.ff_base_url = FF_DEFAULT_URL[self.ff_mode or "agent"]

    def action_ff_test_connection(self):
        """Ajustes: prueba desde el SERVIDOR (que es quien le pega a Factura
        Facil) la URL y las credenciales."""
        self.ensure_one()
        base = (self.ff_base_url or "").rstrip("/")
        cfg = self.sudo()
        try:
            if self.ff_mode == "online":
                # La API en linea no tiene healthcheck: se consulta un documento
                # que no existe. 404 = credenciales validas; 401/403 = invalidas.
                r = requests.get(base + "/api/pac/reception_fe/find_by_cufe_or_id/",
                                 params={"cufe_or_id": "00000000-0000-4000-8000-000000000000"},
                                 headers={"X-FF-Company": cfg.ff_company_uuid or "",
                                          "X-FF-API-Key": cfg.ff_api_key or ""},
                                 timeout=(5, 10))
                ok = r.status_code == 404
                detail = (_("credenciales rechazadas (HTTP %s)", r.status_code)
                          if r.status_code in (401, 403) else _("HTTP %s", r.status_code))
            else:
                r = requests.get(base + "/api/healthcheck/",
                                 headers={"X-FF-Company": cfg.ff_company_uuid or ""},
                                 timeout=(5, 5))
                ok = r.status_code == 200
                detail = _("HTTP %s", r.status_code)
        except requests.RequestException as e:
            ok, detail = False, e.__class__.__name__
        quien = _("La API de Factura Fácil") if self.ff_mode == "online" else _("El agente")
        if ok:
            msg, ntype = _("%(quien)s responde en %(url)s.", quien=quien, url=base), "success"
        else:
            msg, ntype = _("%(quien)s no responde en %(url)s (%(d)s). La URL debe ser "
                           "alcanzable desde el servidor de Odoo, no desde tu PC.",
                           quien=quien, url=base, d=detail), "danger"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": _("Agente Factura Fácil"), "message": msg,
                       "type": ntype, "sticky": not ok},
        }

    def _ff_env_name(self):
        """Nombre de ambiente para los endpoints REST del agente
        (checkRuc / GetResultFe usan 'test' / 'prod')."""
        return "prod" if self.ff_environment == "1" else "test"
