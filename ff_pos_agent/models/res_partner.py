import logging

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .pos_config import FF_CLIENT_MODES

_logger = logging.getLogger(__name__)

# Prefijos de cedula con letra (LOGICA_RUC.md 2.1): PE, E, N, PI, AV y nAV.
FF_NATURAL_PREFIXES = {"PE", "E", "N", "PI", "AV"}


def ff_classify_ruc(ruc):
    """Clasifica un RUC panameno por su estructura (LOGICA_RUC.md 3).

    Es una heuristica local: sirve para pre-llenar la pantalla y ahorrar
    llamadas, nunca para decidir el tratamiento fiscal. La autoridad es el
    `tipoRuc` / `tipoReceptor` que devuelve la DGI al validar.
    Devuelve: "natural" | "juridica" | "gobierno" | "desconocido".
    """
    norm = (ruc or "").strip().upper()
    if not norm:
        return "desconocido"
    segs = [s.strip() for s in norm.split("-")]
    # 1) El literal NT (no tributario) gana sobre todo lo demas.
    if "NT" in segs:
        return "gobierno"
    first = segs[0]
    if not first:
        return "desconocido"
    # 2) Prefijo alfabetico de cedula.
    if first in FF_NATURAL_PREFIXES or (
            first.endswith("AV") and first[:-2].isdigit()):
        return "natural"
    # 3/4) Numerico: 1..13 es provincia (natural); mas alto es ficha o folio.
    if first.isdigit():
        n = int(first)
        if 1 <= n <= 13:
            return "natural"
        if n > 13:
            return "juridica"
    return "desconocido"


class ResPartner(models.Model):
    _inherit = "res.partner"

    ff_receptor_type = fields.Selection(
        [
            ("02", "Consumidor final"),
            ("01", "Contribuyente"),
            ("03", "Gobierno"),
            ("04", "Extranjero"),
        ],
        string="Tipo receptor FE", default="02",
        help="Tipo de receptor para la Factura Electrónica (DGI Panamá).")
    ff_ruc = fields.Char("RUC / Cédula", help="RUC o cédula del receptor (DGI).")
    ff_ruc_type = fields.Selection(
        [("1", "Natural"), ("2", "Jurídico")],
        string="Tipo de RUC", default="2")
    ff_dv = fields.Char("DV", help="Digito verificador del RUC (validado con la DGI).")
    ff_razon_social = fields.Char("Razón social (DGI)")
    ff_location_code = fields.Char(
        "Código ubicación", help="Código provincia-distrito-corregimiento, ej. 8-8-7")
    # OJO: sin readonly=True a nivel de modelo. El widget lo setea en el
    # formulario y el guardado posterior lo descartaria (el write via RPC filtra
    # los campos readonly del modelo). El readonly se fuerza en la vista.
    ff_ruc_validated = fields.Boolean("RUC validado", copy=False)

    ff_ruc_hint = fields.Char(
        "Clasificación", compute="_compute_ff_ruc_hint",
        help="Tipo deducido de la estructura del RUC (heurística local). "
             "La DGI lo confirma al validar.")

    @api.depends("ff_ruc")
    def _compute_ff_ruc_hint(self):
        labels = {
            "natural": _("Persona natural (cédula)"),
            "juridica": _("Persona jurídica (folio / ficha)"),
            "gobierno": _("Gobierno / no tributario (NT)"),
            "desconocido": _("Formato no reconocido - lo decide la DGI"),
        }
        for partner in self:
            partner.ff_ruc_hint = labels[ff_classify_ruc(partner.ff_ruc)] if partner.ff_ruc else False

    @api.onchange("ff_ruc")
    def _onchange_ff_ruc(self):
        """Del RUC se deduce casi todo; sin RUC, consumidor final."""
        for partner in self:
            # Cambiar el RUC invalida lo que habia dicho la DGI del anterior.
            partner.ff_ruc_validated = False
            partner.ff_razon_social = False
            partner.ff_dv = False
            kind = ff_classify_ruc(partner.ff_ruc)
            if not (partner.ff_ruc or "").strip():
                partner.ff_receptor_type = "02"
                continue
            if kind == "gobierno":
                partner.ff_receptor_type = "03"
                partner.ff_ruc_type = "2"
                continue
            # Un receptor extranjero (04) se identifica con su documento de
            # origen: no lo pisamos con la heuristica panamena.
            if partner.ff_receptor_type != "04":
                # Hay RUC y no lleva la marca NT: contribuyente. Si el formato
                # no se reconoce igual se asume 01 (nunca consumidor final) y
                # la DGI corrige el tipo al validar.
                partner.ff_receptor_type = "01"
            if kind in ("natural", "juridica"):
                partner.ff_ruc_type = "1" if kind == "natural" else "2"

    def _ff_ruc_params(self, config_id=None):
        """Con qué conexión consulta el RUC EL SERVIDOR.

        Cada origen tiene la suya, y antes esto solo miraba los puntos de venta:
        quien facturaba desde Contabilidad con la API en línea recibía un
        "no hay agente" aunque su compañía estuviera perfectamente configurada.

        Orden de preferencia:

        1. El punto de venta que lo pide, si lo dice (`config_id`).
        2. La configuración de FACTURAS de la compañía: es la que corresponde
           cuando la consulta nace del backend, y la que suele estar en modo
           "API en línea" — accesible desde el servidor por definición.
        3. Cualquier punto de venta cuyo agente alcance el servidor.

        Se excluye siempre el modo "agente en la caja": su URL (127.0.0.1)
        apunta a la máquina del cajero, no a la de Odoo. Si el servidor la
        usara, se estaría llamando a sí mismo. En ese modo la consulta la hace
        el navegador (ver `ff_ruc_from_agent`).
        """
        # sudo: las credenciales solo las lee un responsable, pero el cajero
        # valida RUCs desde el POS al crear clientes.
        if config_id:
            cfg = self.env["pos.config"].sudo().browse(config_id).exists()
            if cfg and cfg.ff_agent_enabled and cfg.ff_base_url \
                    and cfg.ff_mode not in FF_CLIENT_MODES:
                return self._ff_params_from_pos(cfg)

        company = self.env.company.sudo()
        if company._ff_inv_configured() and company.ff_inv_mode not in FF_CLIENT_MODES:
            return {
                "mode": company.ff_inv_mode or "agent",
                "base_url": company.ff_inv_base_url,
                "env_name": company._ff_inv_env_name(),
                "api_key": company.ff_inv_api_key or "",
                "company_uuid": company.ff_inv_company_uuid or "",
                "origen": "compañía (facturas)",
            }

        cfg = self.env["pos.config"].sudo().search([
            ("ff_agent_enabled", "=", True),
            ("ff_api_key", "!=", False),
            ("ff_base_url", "!=", False),
            ("ff_mode", "not in", list(FF_CLIENT_MODES)),
        ], limit=1)
        if cfg:
            return self._ff_params_from_pos(cfg)

        raise UserError(_(
            "No hay ninguna conexión con Factura Fácil que el servidor pueda "
            "usar para validar el RUC. Configúrala en Ajustes → Contabilidad → "
            "Facturas de cliente, o en un punto de venta cuyo agente sea "
            "alcanzable desde Odoo. (Las cajas en modo 'Agente en la caja' no "
            "sirven aquí: ese agente solo lo alcanza su propio navegador.)"))

    @api.model
    def _ff_params_from_pos(self, cfg):
        return {
            "mode": cfg.ff_mode or "agent",
            "base_url": cfg.ff_base_url,
            "env_name": cfg._ff_env_name(),
            "api_key": cfg.ff_api_key or "",
            "company_uuid": cfg.ff_company_uuid or "",
            "origen": "punto de venta %s" % cfg.name,
        }

    @api.model
    def ff_ruc_from_agent(self, ruc, data):
        """Normaliza la respuesta cruda del agente que consultó la CAJA.

        En modo "agente en la caja" quien llama a checkRuc es el navegador, pero
        la interpretación se queda aquí: es la misma lógica (y los mismos casos
        raros de tipoRuc/tipoReceptor) que ya usa `ff_check_ruc`."""
        return self._ff_parse_check(ruc, data or {})

    @api.model
    def ff_check_ruc(self, ruc, config_id=None):
        """Consulta un RUC en la DGI (via el agente) SIN escribir nada.

        Es `@api.model` a proposito: el dialogo de cliente del POS valida
        mientras se crea la ficha, cuando todavia no hay id que guardar.
        Tampoco levanta excepciones por fallos esperables (agente caido, RUC
        inexistente): devuelve el motivo en `message` para que la pantalla lo
        muestre sin cerrarse.
        """
        ruc = (ruc or "").strip()
        if not ruc:
            return {"valido": False,
                    "message": _("Ingresa el RUC / cédula antes de validar.")}
        try:
            cfg = self._ff_ruc_params(config_id=config_id)
        except UserError as e:
            return {"valido": False, "message": e.args[0] if e.args else str(e)}

        # Tipo "0": que resuelva la DGI. El agente ignora el tipo del path para
        # clasificar (devuelve el tipoRuc real igual), asi que no se le pasa la
        # heuristica local y no hay forma de consultar con el tipo equivocado.
        base = (cfg["base_url"] or "").rstrip("/")
        if cfg["mode"] == "online":
            # API en linea: el gateway lleva la API Key en la URL.
            url = "%s/api/gateway/wp/%s/checkRUC/%s/%s/0" % (
                base, cfg["env_name"], cfg["api_key"], ruc)
        else:
            url = "%s/api/fepa/v1/%s/checkRuc/%s/0" % (base, cfg["env_name"], ruc)
        headers = {
            "X-FF-Company": cfg["company_uuid"],
            "X-FF-API-Key": cfg["api_key"],
        }
        try:
            # El agente devuelve 400 (con JSON valido={false}) cuando el RUC no
            # existe; por eso NO usamos raise_for_status y parseamos el cuerpo.
            r = requests.get(url, headers=headers, timeout=15)
            data = r.json()
        except requests.RequestException as e:
            # El detalle tecnico va al log; al cajero se le dice que hacer.
            # (el mensaje de requests incluye la URL: sin la API Key)
            _logger.warning("[ff_pos_agent] checkRuc %s falló (%s): %s", ruc,
                            cfg["origen"], str(e).replace(cfg["api_key"] or "\0", "***"))
            return {"valido": False,
                    "message": _("No se pudo contactar Factura Fácil en %s. Revisa "
                                 "la conexión y volvé a intentar.") % cfg["base_url"]}
        except ValueError:
            return {"valido": False,
                    "message": _("Respuesta inválida de Factura Fácil al validar el "
                                 "RUC (HTTP %s): %s") % (r.status_code, r.text[:200])}

        return self._ff_parse_check(ruc, data)

    @api.model
    def _ff_parse_check(self, ruc, data):
        """Interpreta la respuesta de checkRuc, venga del servidor o de la caja."""
        if data.get("error"):
            return {"valido": False,
                    "message": _("La DGI rechazó la consulta: %s") % data["error"]}
        if not data.get("valido"):
            return {"valido": False,
                    "message": _("La DGI no reconoce el RUC %s.") % ruc}

        res = {
            "valido": True,
            "ruc": data.get("ruc") or ruc,
            "dv": data.get("dv") or "",
            "razon_social": data.get("razonSocial") or "",
        }
        # tipoRuc / tipoReceptor pueden venir como numero o como string
        # (LOGICA_RUC.md 7); la DGI manda sobre la heuristica local.
        if str(data.get("tipoRuc") or "") in ("1", "2"):
            res["ruc_type"] = str(data["tipoRuc"])
        receptor = str(data.get("tipoReceptor") or "").zfill(2)
        # Un RUC que la DGI reconoce nunca es consumidor final.
        res["receptor_type"] = receptor if receptor in ("01", "03", "04") else "01"
        afil = _(" (afiliado a FE)") if data.get("iAfilFE") else ""
        res["message"] = _("%(name)s - DV %(dv)s%(afil)s") % {
            "name": res["razon_social"] or ruc,
            "dv": res["dv"] or "?",
            "afil": afil,
        }
        return res

    def action_ff_validate_ruc(self):
        """Version backend del boton (el POS usa el widget `ff_ruc_validator`)."""
        self.ensure_one()
        if not self.ff_ruc:
            raise UserError(_("Ingresa el RUC / cédula antes de validar."))
        res = self.ff_check_ruc(self.ff_ruc)
        if not res.get("valido"):
            self.ff_ruc_validated = False
            return self._ff_notify(_("RUC no válido"), res["message"], "warning")
        self.write(self._ff_vals_from_check(res))
        return self._ff_notify(_("RUC válido"), res["message"], "success")

    @api.model
    def _ff_vals_from_check(self, res):
        """Respuesta de `ff_check_ruc` -> valores de la ficha."""
        vals = {"ff_ruc_validated": True}
        if res.get("dv"):
            vals["ff_dv"] = res["dv"]
        if res.get("razon_social"):
            vals["ff_razon_social"] = res["razon_social"]
            # El nombre oficial de la DGI manda: asi debe llamarse el cliente en
            # la factura, y le quita al cajero la oportunidad de equivocarse.
            vals["name"] = res["razon_social"]
        if res.get("ruc_type"):
            vals["ff_ruc_type"] = res["ruc_type"]
        if res.get("receptor_type"):
            vals["ff_receptor_type"] = res["receptor_type"]
        return vals

    def _ff_notify(self, title, message, ntype):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "message": message, "type": ntype, "sticky": False},
        }
