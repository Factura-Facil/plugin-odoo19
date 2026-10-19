"""Documento fiscal electronico emitido por el agente de Factura Facil.

Es el ciclo de vida del envio, independiente de quien lo origina (hoy
`pos.order` o `account.move`): estado, CUFE, auditoria, HTTP con el
agente, idempotencia, bloqueo y cron. La fuente solo aporta tres cosas:

- `_ff_agent_config()`: con que agente habla (url, credenciales, ambiente).
- `_ff_build_payload()`: el documento `{header, document}`; lanza UserError
  si no se puede expresar como documento fiscal valido.
- `_ff_cafe_payload(rfe)`: los datos ya formateados para imprimir el CAFE.
"""
import base64
import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

import psycopg2
import requests
from urllib3.exceptions import NewConnectionError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .pos_config import FF_CLIENT_MODES

_logger = logging.getLogger(__name__)

FF_ENDPOINT = "/api/pac/reception_fe/detailed/"
# La impresion del CAFE NO es una bandera del documento: es su propia ruta,
# con la api_key en la URL (el body de reception_fe solo acepta
# {header, document} y descarta en silencio cualquier otra clave raiz).
FF_PRINT_ENDPOINT = "/fe/rest/api/printByCufe/%s"
FF_HEALTH_ENDPOINT = "/api/healthcheck/"
# API en linea: consulta por CUFE o UUID, y el PDF oficial del CAFE.
FF_ONLINE_FIND = "/api/pac/reception_fe/find_by_cufe_or_id/"
FF_ONLINE_CAFE = "/api/pac/reception_fe/%s/download_cafe/"
# find_by_cufe_or_id.status (sin documentar): 3 = "Finalizado" (autorizado en
# QA). Los negativos se tratan como rechazo; el resto, en proceso.
FF_ONLINE_DONE = 3
FF_CONNECT_TIMEOUT = 5
FF_HEALTH_TIMEOUT = 3
FF_READ_TIMEOUT = 45
FF_POLL_TRIES = 6
FF_POLL_WAIT = 0.7
FF_POLL_TIMEOUT = 10
# Si nadie pide la emision tras crear el documento, el cron la hace a los 2 min.
FF_CRON_FALLBACK = 120
# Cuanto espera el POS a que otro proceso (el cron) suelte el documento.
FF_LOCK_WAIT = "30s"

FE_ESTADOS = [
    ("pendiente", "Pendiente"),
    ("incierta", "Envío incierto"),
    ("timbrada", "Timbrada (firmada)"),
    ("autorizada", "Autorizada DGI"),
    ("rechazada", "Rechazada DGI"),
    ("error", "Error"),
]

# Idempotencia: el agente NO deduplica. Reenviar el mismo payload (incluso con
# el mismo fd_number) crea OTRO documento fiscal con el mismo numero y otro
# CUFE (probado contra el agente real). Toda la idempotencia vive en Odoo:
# solo se reenvia cuando es seguro que el primer POST no llego.


def _ff_not_delivered(exc):
    """True solo si es seguro que la peticion NO llego al agente: conexion
    rechazada, DNS o timeout al conectar. Un corte a mitad de la respuesta o
    un timeout de lectura es incierto (el agente pudo haberla procesado)."""
    if isinstance(exc, requests.ConnectTimeout):
        return True
    if isinstance(exc, requests.ConnectionError) and exc.args:
        return isinstance(getattr(exc.args[0], "reason", None), NewConnectionError)
    return False


class FfFeDocument(models.Model):
    _name = "ff.fe.document"
    _description = "Documento FE (Factura Fácil)"
    _order = "id desc"

    name = fields.Char("SystemRef", required=True, index=True,
                       help="Referencia del documento en el agente (header.id).")
    pos_order_id = fields.Many2one("pos.order", "Orden POS", index="btree_not_null",
                                   ondelete="restrict")
    account_move_id = fields.Many2one("account.move", "Factura", index="btree_not_null",
                                      ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, index=True,
                                 default=lambda self: self.env.company)

    fe_estado = fields.Selection(FE_ESTADOS, "Estado FE", default="pendiente",
                                 required=True, index=True)
    fe_cufe = fields.Char("CUFE", index="btree_not_null")
    fe_nro_dgi = fields.Char("Nro. Documento FE")
    fe_protocolo = fields.Char("Protocolo autorización")
    fe_confirmacion = fields.Char("Nro. confirmación")
    fe_error = fields.Text("Detalle error / mensajes DGI")
    fe_qr_base64 = fields.Text("QR (base64)")
    fe_qr_url = fields.Char("URL consulta DGI (QR)")
    fe_document_uuid = fields.Char("UUID documento (agente)")
    fe_doc_json = fields.Text("Datos CAFE (JSON)")
    # --- Auditoria: queda registro del intento aunque no se firme ---
    fe_request_json = fields.Text("Payload enviado al agente")
    fe_response_json = fields.Text("Respuesta del agente")
    fe_request_date = fields.Datetime("Último envío al agente")
    fe_impreso = fields.Boolean("CAFE impreso por el agente")
    # API en linea: el PDF oficial del CAFE, adjunto a la orden.
    fe_cafe_pdf_id = fields.Many2one("ir.attachment", "CAFE oficial (PDF)", readonly=True)

    # Odoo 19 ya no aplica _sql_constraints: sin esto, nada impedia que una
    # misma orden (o factura) acabara con dos documentos fiscales.
    _pos_order_uniq = models.Constraint(
        "unique(pos_order_id)",
        "Una orden POS tiene un solo documento FE.",
    )
    _account_move_uniq = models.Constraint(
        "unique(account_move_id)",
        "Una factura tiene un solo documento FE.",
    )

    def write(self, vals):
        # El estado fiscal solo lo escribe el flujo de emision (con sudo). Un
        # usuario solo puede vincular el UUID de un documento que encontro en
        # el agente cuando el envio quedo incierto (ver la vista de la orden).
        if not self.env.su:
            if set(vals) - {"fe_document_uuid"}:
                raise AccessError(_("El estado de la factura electrónica no se edita a mano."))
            if any(doc.fe_estado != "incierta" for doc in self):
                raise AccessError(_("Solo se puede vincular un documento del agente a un "
                                    "envío incierto."))
        return super().write(vals)

    def _ff_set(self, vals):
        """Escritura del propio flujo de emision. sudo() y no un flag de
        contexto: el contexto lo controla el cliente por RPC, sudo() no."""
        return self.sudo().write(vals)

    # ------------------------------------------------------------------
    # Fuente del documento
    # ------------------------------------------------------------------
    def _ff_source(self):
        """Quien origina el documento: hoy una orden POS o una factura."""
        self.ensure_one()
        return self.pos_order_id or self.account_move_id

    def _ff_cfg(self):
        """Conexion con el agente que aporta la fuente (ver docstring)."""
        return self._ff_source()._ff_agent_config()

    @api.model
    def _ff_schedule_cron(self):
        self.env.ref("ff_pos_agent.ir_cron_ff_update_status")._trigger(
            fields.Datetime.now() + timedelta(seconds=FF_CRON_FALLBACK))

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _ff_base(self):
        return (self._ff_cfg()["base_url"] or "").rstrip("/")

    def _ff_headers(self):
        cfg = self._ff_cfg()
        return {
            "Content-Type": "application/json",
            "X-FF-Company": cfg["company_uuid"] or "",
            "X-FF-API-Key": cfg["api_key"] or "",
        }

    # ------------------------------------------------------------------
    # Auditoria
    # ------------------------------------------------------------------
    def _ff_log_request(self, payload):
        """Deja el payload exacto que sale hacia el agente, se firme o no. Se
        escribe ANTES del POST para que quede aunque todo falle. La respuesta
        NO se borra: el historial de intentos es parte de la auditoria (sobre
        todo si un documento se reemitio)."""
        now = fields.Datetime.now()
        self._ff_set({
            "fe_request_json": json.dumps(payload, indent=2, ensure_ascii=False),
            "fe_request_date": now,
        })
        self._ff_log_append("=== Envío %s ===" % now)

    def _ff_log_append(self, entry):
        # La api_key viaja en la URL de printByCufe y los errores de red de
        # requests incluyen la URL: nunca debe quedar en la auditoria.
        api_key = self._ff_cfg()["api_key"]
        if api_key:
            entry = entry.replace(api_key, "***")
        prev = self.fe_response_json or ""
        # Se conserva lo mas reciente si el historial crece demasiado.
        self._ff_set({"fe_response_json": ((prev + "\n\n" + entry) if prev else entry)[-60000:]})

    def _ff_log_response(self, status, body, label=None):
        entry = "HTTP %s\n%s" % (status, body)
        if label:
            entry = "--- %s ---\n%s" % (label, entry)
        self._ff_log_append(entry)

    def _ff_mark_incierta(self, detail):
        msg = _("Envío incierto (%(d)s): el agente pudo haber creado el documento "
                "sin confirmarlo. NO se reenvia solo, para no duplicar la factura "
                "en la DGI. Buscalo en el agente por SystemRef %(ref)s: si existe, "
                "pega su UUID en la orden y pulsa Reintentar; si no, usa "
                "'Forzar reenvío'.", d=detail, ref=self.name)
        self._ff_set({"fe_estado": "incierta", "fe_error": msg})

    # ------------------------------------------------------------------
    # Emision
    # ------------------------------------------------------------------
    def _ff_sign(self):
        """Emite el documento en el agente, o lo retoma si ya existe.

        Regla de idempotencia: con `fe_document_uuid` NUNCA se vuelve a hacer
        POST (el agente no deduplica); solo se consulta ese documento."""
        self.ensure_one()
        if self.fe_document_uuid:
            return self._ff_resume()
        try:
            payload = self._ff_source()._ff_build_payload()
        except UserError as e:
            # No se puede expresar como documento fiscal valido (p.ej. un
            # descuento mayor que las ventas de su tasa): no reintentar.
            self._ff_set({"fe_estado": "error", "fe_error": str(e)})
            return
        # El registro de auditoria va primero: si el envio se aborta o revienta,
        # el payload ya quedo guardado.
        self._ff_log_request(payload)
        # Sondeo previo: si el agente no responde, el POST nunca sale y el
        # documento queda 'pendiente' (reenvio seguro). No basta con el error de
        # conexion del POST: el proxy de Docker Desktop (host.docker.internal)
        # acepta la conexion aunque el agente este apagado y luego se cuelga,
        # lo que se veria como un envio incierto tras 45 s de espera.
        # (La API en linea no tiene healthcheck; y sin proxy de Docker de por
        # medio, un error de conexion del POST ya dice si el envio no salio.)
        alive, detail = (True, "") if self._ff_online() else self._ff_agent_alive()
        if not alive:
            self._ff_log_response("-", detail, label="healthcheck")
            self._ff_set({"fe_estado": "pendiente", "fe_error": _(
                "El agente no responde (%s). La factura no se envió; se reintenta sola.", detail)})
            return
        # Marca previa, con commit: si el proceso muere durante el POST
        # (worker matado por limit_time_real, reinicio de Odoo), el documento
        # queda 'incierta' y nadie lo reenvia solo. El resultado real la pisa.
        self._ff_mark_incierta(_("se interrumpió el envío antes de la respuesta del agente"))
        self._ff_commit()
        try:
            resp = requests.post(
                self._ff_base() + FF_ENDPOINT, headers=self._ff_headers(),
                data=json.dumps(payload),
                timeout=(FF_CONNECT_TIMEOUT, FF_READ_TIMEOUT))
        except requests.RequestException as e:
            self._ff_log_response("-", str(e))
            if _ff_not_delivered(e):
                # No llego: reenviar es seguro (boton Reintentar o cron).
                self._ff_set({"fe_estado": "pendiente", "fe_error": str(e)})
            else:
                self._ff_mark_incierta(e.__class__.__name__)
            return

        try:
            body = json.dumps(resp.json(), indent=2, ensure_ascii=False)
        except ValueError:
            body = resp.text or ""
        self._ff_log_response(resp.status_code, body)

        if resp.status_code >= 500:
            # El agente pudo haber creado el documento antes de fallar.
            self._ff_mark_incierta("HTTP %s: %s" % (resp.status_code, self._ff_readable_error(resp)))
            return
        if resp.status_code not in (200, 201):
            # 4xx: validacion/credenciales; el agente no creo nada.
            self._ff_set({"fe_estado": "error", "fe_error": self._ff_readable_error(resp)})
            return

        try:
            data = resp.json()
        except ValueError:
            data = {}
        # Si el agente devuelve el DocumentResult completo y avisa duplicado,
        # el documento valido es el original.
        original = data.get("original") if data.get("duplicated") else None
        doc_uuid = (original or {}).get("document_uuid") or data.get("document_uuid")
        if not doc_uuid:
            self._ff_mark_incierta(_("HTTP %s sin document_uuid", resp.status_code))
            return
        # El UUID se guarda antes que nada: desde aqui cualquier reintento
        # retoma este documento en vez de crear otro. Y desde aqui no se puede
        # lanzar: una excepcion haria rollback del UUID y el siguiente intento
        # emitiria la factura otra vez.
        self._ff_set({
            "fe_document_uuid": doc_uuid,
            "fe_estado": "pendiente",
            "fe_error": _("Documento creado en Factura Fácil; recuperando el CUFE."),
        })
        self._ff_commit()
        try:
            if self._ff_online():
                # La API en linea responde sincrono: autorizado o rechazado.
                self._ff_online_apply(original or data)
            else:
                self._ff_resume()
        except Exception as e:  # noqa: BLE001
            _logger.exception("[ff_pos_agent] documento %s creado; fallo al recuperarlo", doc_uuid)
            self._ff_set({"fe_estado": "pendiente", "fe_error": str(e)})

    def _ff_commit(self):
        """Deja firme el estado del envio. _ff_sign solo corre en su propia
        transaccion (cron, ff_emitir_data, botones), nunca dentro de la
        sincronizacion de la venta. Libera el bloqueo de la fila: desde aqui
        lo que protege contra un doble envio es el estado ya guardado."""
        self.env.cr.commit()

    def _ff_agent_alive(self):
        """(True, "") si el agente contesta su healthcheck con 200."""
        try:
            r = requests.get(self._ff_base() + FF_HEALTH_ENDPOINT, headers=self._ff_headers(),
                             timeout=(FF_CONNECT_TIMEOUT, FF_HEALTH_TIMEOUT))
        except requests.RequestException as e:
            return False, e.__class__.__name__
        if r.status_code != 200:
            return False, "healthcheck HTTP %s" % r.status_code
        return True, ""

    def _ff_online(self):
        return self._ff_cfg()["mode"] == "online"

    def _ff_resume(self):
        """Trae la firma (CUFE) del documento ya creado en el agente y, si la
        hay, el resultado del PAC. Nunca crea documentos."""
        self.ensure_one()
        if self._ff_online():
            return self._ff_online_resume()
        rfe = self._ff_fetch_rfe(self._ff_base(), self._ff_headers(), self.fe_document_uuid)
        self._ff_apply_signed(rfe, self.fe_document_uuid)
        # Intentar traer ya el resultado del PAC (autorizacion / rechazo)
        if self.fe_cufe:
            try:
                self._ff_query_result()
            except Exception:  # noqa: BLE001 - el estado final lo captura el cron
                _logger.info("[ff_pos_agent] resultado PAC aún no disponible para %s", self.fe_cufe)
            self._ff_maybe_print()

    def _ff_maybe_print(self):
        if self._ff_online():
            # Sin agente no hay impresora: el CAFE sale con el recibo de Odoo y
            # el PDF oficial queda adjunto (tambien desde el cron).
            if not self.fe_cafe_pdf_id and self.fe_estado in ("timbrada", "autorizada"):
                self._ff_download_cafe()
            return
        # "Agente en la caja": la impresora cuelga de esa maquina y el servidor
        # no la alcanza. La impresion la manda el POS (ver ff_local.js).
        if self._ff_cfg()["mode"] in FF_CLIENT_MODES:
            return
        # El cron no imprime: el cliente ya se fue y la impresora es la de la
        # caja. fe_impreso evita imprimir dos veces el mismo CAFE.
        if (self._ff_cfg()["print_cafe"] and not self.fe_impreso
                and not self.env.context.get("ff_no_print")):
            self._ff_print_cafe()

    def _ff_print_cafe(self):
        """Manda imprimir el CAFE en la impresora del agente. Nunca rompe la
        venta: el fallo solo queda en la auditoria."""
        self.ensure_one()
        if not self.fe_cufe:
            return False
        url = self._ff_base() + FF_PRINT_ENDPOINT % (self._ff_cfg()["api_key"] or "")
        try:
            r = requests.post(
                url, headers={"Content-Type": "application/json"},
                data=json.dumps({"cufe": self.fe_cufe}),
                timeout=(FF_CONNECT_TIMEOUT, FF_READ_TIMEOUT))
        except requests.RequestException as e:
            self._ff_log_response("-", str(e), label="printByCufe")
            return False
        self._ff_log_response(r.status_code, (r.text or "")[:2000], label="printByCufe")
        ok = r.status_code == 200
        if ok:
            self._ff_set({"fe_impreso": True})
        else:
            _logger.warning("[ff_pos_agent] el agente no imprimio el CAFE %s (HTTP %s)",
                            self.fe_cufe, r.status_code)
        return ok

    def _ff_fetch_rfe(self, base, headers, doc_uuid):
        url = "%s/api/document/%s/" % (base, doc_uuid)
        for _i in range(FF_POLL_TRIES):
            try:
                r = requests.get(url, headers=headers,
                                 timeout=(FF_CONNECT_TIMEOUT, FF_POLL_TIMEOUT))
                if r.status_code == 200:
                    rfe = (r.json() or {}).get("xml_data", {}).get("rFE")
                    if rfe:
                        return rfe
            except (requests.RequestException, ValueError):
                pass
            time.sleep(FF_POLL_WAIT)
        return None

    def _ff_apply_signed(self, rfe, doc_uuid):
        if not rfe:
            self._ff_set({
                "fe_estado": "pendiente",
                "fe_document_uuid": doc_uuid or False,
                "fe_error": "Documento creado; firma/CUFE aún pendiente en el agente.",
            })
            return
        cufe = rfe.get("dId")
        nro = (rfe.get("gDGen") or {}).get("dNroDF")
        qr_url = (rfe.get("gNoFirm") or {}).get("dQRCode")
        self._ff_set({
            "fe_estado": "timbrada",
            "fe_cufe": cufe,
            "fe_nro_dgi": nro,
            "fe_qr_url": qr_url or False,
            "fe_document_uuid": doc_uuid or False,
            "fe_error": False,
            "fe_qr_base64": self._ff_make_qr(qr_url or cufe),
            "fe_doc_json": json.dumps(self._ff_source()._ff_cafe_payload(rfe)),
        })

    def _ff_query_result(self):
        """Consulta el resultado del PAC (GetResultFe) y actualiza el estado."""
        self.ensure_one()
        if not self.fe_cufe:
            return
        if self._ff_online():
            return self._ff_online_status(self._ff_online_find(self.fe_cufe))
        cfg = self._ff_cfg()
        url = "%s/api/fepa/v1/%s/GetResultFe/%s/%s" % (
            self._ff_base(), cfg["env_name"], cfg["company_uuid"], self.fe_cufe)
        r = requests.get(url, headers=self._ff_headers(),
                         timeout=(FF_CONNECT_TIMEOUT, FF_READ_TIMEOUT))
        if r.status_code != 200:
            return
        return self._ff_apply_result_data(r.json())

    def _ff_apply_result_data(self, res):
        """Aplica un GetResultFe ya obtenido. Lo separa de la peticion para que
        tambien pueda alimentarlo la caja en el modo 'agente en la caja', donde
        quien consulta al agente es el navegador."""
        self.ensure_one()
        res = res or {}
        vals = {}
        # QR oficial de la DGI (con digestValue + jwt) si viene
        if res.get("qrContent"):
            vals["fe_qr_url"] = res["qrContent"]
            vals["fe_qr_base64"] = self._ff_make_qr(res["qrContent"])
        if res.get("confirmationNbr"):
            vals["fe_confirmacion"] = res["confirmationNbr"]
        err = (res.get("dgiErrMsg") or "").strip()
        err = "" if err in ("", "None") else err
        if res.get("authorized"):
            vals.update({
                "fe_estado": "autorizada",
                "fe_protocolo": res.get("authNumber") or self.fe_protocolo,
                "fe_error": False,
            })
        elif res.get("received") and res.get("accepted") is False:
            msg = err or res.get("dgiResp") or res.get("msg") or "Rechazado por la DGI"
            vals.update({"fe_estado": "rechazada", "fe_error": msg})
        elif err:
            vals.update({"fe_estado": "rechazada", "fe_error": err})
        # si no, sigue 'timbrada' (firmada, pendiente de autorizacion del PAC)
        if vals:
            self._ff_set(vals)

    def _ff_readable_error(self, resp):
        """Extrae un mensaje legible de la respuesta de error del agente:
        errores de validacion DRF (JSON) o la excepcion de la pagina HTML."""
        try:
            data = resp.json()
            flat = self._ff_flatten_errors(data)
            if flat:
                return flat[:500]
        except ValueError:
            pass
        txt = resp.text or ""
        m = re.search(r"<title>(.*?)</title>", txt, re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()[:200]
        return txt[:300]

    def _ff_flatten_errors(self, data, prefix=""):
        """Aplana {'document':{'receptor':{'ruc':['msg']}}} -> 'receptor.ruc: msg'."""
        if isinstance(data, dict):
            parts = []
            for k, v in data.items():
                key = k if k in ("document", "header") else (prefix + k)
                sub = self._ff_flatten_errors(v, "" if k in ("document", "header") else key + ".")
                if sub:
                    parts.append(sub)
            return " | ".join(p for p in parts if p)
        if isinstance(data, (list, tuple)):
            msg = "; ".join(str(x) for x in data)
            return "%s%s" % (prefix.rstrip("."), (": " + msg) if prefix else msg)
        return "%s%s" % (prefix, data) if data else ""

    def _ff_make_qr(self, content):
        if not content:
            return False
        try:
            import qrcode
            img = qrcode.make(content)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001 - el QR es opcional
            return False

    # ------------------------------------------------------------------
    # API en linea
    # ------------------------------------------------------------------
    def _ff_online_find(self, cufe_or_id):
        """find_by_cufe_or_id: estado + XML firmado (rFE). None si no se pudo."""
        try:
            r = requests.get(self._ff_base() + FF_ONLINE_FIND, params={"cufe_or_id": cufe_or_id},
                             headers=self._ff_headers(), timeout=(FF_CONNECT_TIMEOUT, FF_POLL_TIMEOUT))
            return r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError):
            return None

    @api.model
    def _ff_rfe_from_xml(self, xml):
        """<rFE> firmado -> el mismo dict que devuelve el agente en
        xml_data.rFE (hijos por nombre, sin namespaces; repetidos en lista).
        El `xml` de la respuesta de emision viene truncado (6000 caracteres):
        se usa el xml_data de find_by_cufe_or_id."""
        if not xml:
            return None
        try:
            root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
        except ET.ParseError:
            return None

        def conv(el):
            kids = list(el)
            if not kids:
                return (el.text or "").strip()
            out = {}
            for k in kids:
                tag = k.tag.split("}")[-1]
                val = conv(k)
                if tag in out:
                    out[tag] = out[tag] if isinstance(out[tag], list) else [out[tag]]
                    out[tag].append(val)
                else:
                    out[tag] = val
            return out
        return conv(root)

    def _ff_online_apply(self, res):
        """DocumentResult de la API en linea (o su `original` si era duplicado)."""
        rfe = None
        found = self._ff_online_find(self.fe_document_uuid)
        if found:
            rfe = self._ff_rfe_from_xml(found.get("xml_data"))
        if rfe:
            self._ff_apply_signed(rfe, self.fe_document_uuid)
        elif res.get("cufe"):
            # Sin el XML no se arma el CAFE: el cron lo retoma (find por UUID).
            self._ff_set({"fe_cufe": res["cufe"], "fe_nro_dgi": res.get("document_number") or False,
                          "fe_estado": "pendiente",
                          "fe_error": _("Documento emitido; recuperando sus datos para el CAFE.")})
            return
        vals = {}
        qr = res.get("qr_code_data")
        if qr:
            b64 = (res.get("qr_image_b64") or "").split(",", 1)[-1]
            vals.update(fe_qr_url=qr, fe_qr_base64=b64 or self._ff_make_qr(qr))
        msgs = "; ".join("%s: %s" % (m.get("code"), m.get("message"))
                         for m in (res.get("messages") or []) if isinstance(m, dict))
        if res.get("rejected"):
            vals.update(fe_estado="rechazada", fe_error=msgs or _("Rechazado por la DGI"))
        elif res.get("authorization_number"):
            vals.update(fe_estado="autorizada", fe_protocolo=res["authorization_number"], fe_error=False)
        if vals:
            self._ff_set(vals)
        self._ff_maybe_print()

    def _ff_online_resume(self):
        found = self._ff_online_find(self.fe_document_uuid)
        rfe = self._ff_rfe_from_xml((found or {}).get("xml_data"))
        self._ff_apply_signed(rfe, self.fe_document_uuid)
        if rfe:
            self._ff_online_status(found)
            self._ff_maybe_print()

    def _ff_online_status(self, found):
        if not found:
            return
        status = found.get("status")
        if status == FF_ONLINE_DONE:
            self._ff_set({"fe_estado": "autorizada", "fe_error": False})
        elif isinstance(status, int) and status < 0:
            self._ff_set({"fe_estado": "rechazada",
                          "fe_error": found.get("status_display") or _("Rechazado por la DGI")})

    def _ff_download_cafe(self):
        """PDF oficial del CAFE (download_cafe) adjunto a la fuente. No pide
        credenciales: el UUID + CUFE del documento hacen de llave."""
        self.ensure_one()
        url = self._ff_base() + FF_ONLINE_CAFE % self.fe_document_uuid
        try:
            r = requests.get(url, params={"cufe": self.fe_cufe},
                             timeout=(FF_CONNECT_TIMEOUT, FF_READ_TIMEOUT))
        except requests.RequestException as e:
            self._ff_log_response("-", str(e), label="download_cafe")
            return False
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            self._ff_log_response(r.status_code, (r.text or "")[:500], label="download_cafe")
            return False
        src = self._ff_source()
        att = self.env["ir.attachment"].sudo().create({
            "name": "CAFE %s.pdf" % (self.fe_nro_dgi or self.fe_cufe),
            "datas": base64.b64encode(r.content),
            "mimetype": "application/pdf",
            "res_model": src._name,
            "res_id": src.id,
        })
        self._ff_set({"fe_cafe_pdf_id": att.id})
        self._ff_log_response(200, "%s bytes" % len(r.content), label="download_cafe")
        return att

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_ff_update_status(self, limit=100):
        """Cada 10 min (y 2 min despues de cada venta): emite los 'pendiente'
        (no llegaron al agente, o se crearon sin CUFE todavia) y consulta el
        PAC de los 'timbrada'.

        Los 'incierta' NO se tocan: reenviarlos podria duplicar la factura."""
        docs = self.search([
            "|",
            ("fe_estado", "=", "pendiente"),
            "&", ("fe_estado", "=", "timbrada"), ("fe_cufe", "!=", False),
        ], order="id", limit=limit)
        caidos = set()  # agentes sin respuesta: no insistir en esta pasada
        for doc in docs.with_context(ff_no_print=True):
            cfg = doc._ff_cfg()
            if not cfg["enabled"]:
                continue
            # "Agente en la caja": el agente vive en 127.0.0.1 de la caja y el
            # servidor no lo alcanza. Esos documentos los reintenta el POS.
            if cfg["mode"] in FF_CLIENT_MODES:
                continue
            base = doc._ff_base()
            if base in caidos or not doc._ff_lock():
                continue
            # Sin savepoint a proposito: si algo falla despues de un POST
            # aceptado, el UUID ya escrito debe llegar al commit.
            try:
                if doc.fe_estado == "pendiente":
                    doc._ff_sign()
                    if doc.fe_estado == "pendiente" and not doc.fe_document_uuid:
                        caidos.add(base)
                else:
                    doc._ff_query_result()
            except Exception:  # noqa: BLE001
                _logger.exception("[ff_pos_agent] cron: no se pudo actualizar el documento %s", doc.id)
            # Commit por documento: el estado de cada envio queda firme aunque
            # uno posterior falle, y se libera el bloqueo de la fila.
            self.env.cr.commit()

    def _ff_lock(self, wait=False):
        """Bloquea la fila del documento mientras se habla con el agente, para
        que el cron y el POS no lo emitan a la vez.

        `wait`: el POS espera a que el cron termine (y luego ve el documento ya
        emitido) en vez de fallar; el cron nunca espera, salta el documento."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint(flush=False):
                if wait:
                    self.env.cr.execute("SET LOCAL lock_timeout = %s", [FF_LOCK_WAIT])
                    self.env.cr.execute("SELECT id FROM ff_fe_document WHERE id = %s FOR UPDATE", [self.id])
                    self.env.cr.execute("SET LOCAL lock_timeout = DEFAULT")
                else:
                    self.env.cr.execute(
                        "SELECT id FROM ff_fe_document WHERE id = %s FOR UPDATE NOWAIT", [self.id])
        except psycopg2.errors.LockNotAvailable:
            return False
        # Lo que otro proceso haya escrito mientras se esperaba el bloqueo.
        self.invalidate_recordset()
        return True

    # ------------------------------------------------------------------
    # Reintento manual (boton del recibo POS, boton backend, supervisor)
    # ------------------------------------------------------------------
    def _ff_retry(self, wait=False):
        self.ensure_one()
        if not self._ff_cfg()["enabled"]:
            return
        if not self._ff_lock(wait=wait):
            raise UserError(_("Otro proceso está enviando este documento al agente. "
                              "Intenta de nuevo en unos segundos."))
        if self.fe_estado in ("pendiente", "error"):
            # _ff_sign retoma el documento si ya hay UUID; si no, emite.
            self._ff_sign()
        elif self.fe_estado == "incierta":
            # Solo se retoma si un supervisor vinculo el UUID del documento
            # que encontro en el agente. Sin UUID no se reenvia.
            if self.fe_document_uuid:
                self._ff_sign()
        elif self.fe_estado == "rechazada":
            # La DGI rechazo el documento: hace falta uno NUEVO (el rechazado
            # no tiene validez). Queda en la auditoria.
            self._ff_log_append("=== Reemisión tras rechazo DGI (CUFE anterior %s) ===" % (self.fe_cufe or "-"))
            self._ff_reset()
            self._ff_sign()
        elif self.fe_estado == "timbrada":
            # Ya firmada: solo reconsultar el estado del PAC. No se re-emite,
            # pero si la impresion habia fallado se reintenta (fe_impreso evita
            # imprimir dos veces el mismo CAFE).
            self._ff_query_result()
            self._ff_maybe_print()
        elif self.fe_estado == "autorizada":
            # Solo lo pendiente de salida: la impresion en el agente o, en
            # linea, el PDF oficial si no se pudo descargar.
            self._ff_maybe_print()

    def _ff_reset(self):
        self._ff_set({
            "fe_document_uuid": False, "fe_cufe": False, "fe_nro_dgi": False,
            "fe_protocolo": False, "fe_confirmacion": False, "fe_qr_url": False,
            "fe_qr_base64": False, "fe_doc_json": False, "fe_impreso": False,
        })

    def _ff_force_resend(self):
        """Supervisor: el envio quedo incierto y verifico en el agente que el
        documento NO existe. Emite de nuevo (si existiera, quedaria duplicado)."""
        self.ensure_one()
        if not self.env.user.has_group(self._ff_source()._ff_manager_group()):
            raise UserError(_("No tienes permiso para forzar el reenvío de este documento."))
        if self.fe_estado != "incierta" or self.fe_document_uuid:
            raise UserError(_("Solo se puede forzar el reenvío de un documento con envío "
                              "incierto y sin documento vinculado."))
        # La marca 'incierta' se pone ANTES del POST: durante el envio en curso
        # el documento ya se ve incierto. Forzar ahi duplicaria la factura.
        margen = FF_CONNECT_TIMEOUT + FF_READ_TIMEOUT + 30
        if self.fe_request_date and fields.Datetime.now() < self.fe_request_date + timedelta(seconds=margen):
            raise UserError(_("El último envío fue hace menos de %s segundos y puede seguir "
                              "en curso. Espera y vuelve a revisar.", margen))
        if not self._ff_lock():
            raise UserError(_("Otro proceso está enviando este documento al agente. "
                              "Intenta de nuevo en unos segundos."))
        self._ff_log_append("=== Reenvio forzado por %s ===" % self.env.user.name)
        self._ff_sign()

    def _ff_notify_estado(self):
        estado = dict(FE_ESTADOS).get(self.fe_estado, self.fe_estado)
        if self.fe_estado in ("autorizada", "timbrada"):
            ntype = "success"
            msg = _("Estado: %(estado)s. CUFE: %(cufe)s", estado=estado, cufe=self.fe_cufe or "")
        else:
            ntype = "danger"
            msg = _("Estado: %(estado)s. %(err)s", estado=estado, err=(self.fe_error or "")[:400])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Factura Electrónica"),
                "message": msg,
                "type": ntype,
                "sticky": ntype == "danger",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
