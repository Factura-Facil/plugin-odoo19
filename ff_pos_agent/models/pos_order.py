import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    """La orden POS como fuente de un documento FE (ver ff.fe.source)."""
    _name = "pos.order"
    _inherit = ["pos.order", "ff.fe.source"]

    fe_online = fields.Boolean(compute="_compute_fe_online")

    @api.depends("config_id.ff_mode")
    def _compute_fe_online(self):
        for order in self:
            order.fe_online = order.config_id.ff_mode == "online"

    @api.depends("state", "fe_document_id",
                 "config_id.ff_agent_enabled", "config_id.ff_base_url")
    def _compute_ff_puede_firmar(self):
        return super()._compute_ff_puede_firmar()

    # NOTA: NO sobreescribir `_load_pos_data_fields` en pos.order (romperia las
    # relaciones payment_ids/lines -> taxTotals). Con [] el POS carga todos los
    # campos, incluidos los fe_* (el many2one a ff.fe.document lo ignora: el
    # POS no carga ese modelo).

    # ------------------------------------------------------------------
    # Sincronizacion: la orden se guarda SIN hablar con el agente
    # ------------------------------------------------------------------
    def write(self, vals):
        # Los fe_* son espejo del documento FE: nadie los escribe en la orden
        # (el ORM guardaria el valor en la columna aunque sea related). Solo
        # el UUID pasa al documento, que valida quien y cuando.
        if not self.env.su:
            vals = {k: v for k, v in vals.items()
                    if not k.startswith("fe_") or k == "fe_document_uuid"}
        return super().write(vals)

    def _process_order(self, order, existing_order):
        # El estado FE solo lo escribe el servidor. El POS reenvia todos sus
        # campos al sincronizar y no debe poder decidir el estado fiscal.
        for key in [k for k in order if k.startswith("fe_")]:
            order.pop(key)
        return super()._process_order(order, existing_order)

    def _process_saved_order(self, draft):
        # Por aqui pasa toda orden que se paga: desde el POS y desde el
        # asistente de pago del backend (devoluciones hechas en el backend).
        res = super()._process_saved_order(draft)
        if self._ff_emitible() and not self.fe_document_id:
            # Sin HTTP: la emision va en su propia transaccion. El POS llama a
            # ff_emitir_data justo despues de sincronizar (para el CAFE del
            # recibo); si nunca llama (navegador cerrado, caja offline), el
            # cron la emite. Si esta transaccion se deshace, no queda un
            # documento fiscal huerfano en el agente.
            self._ff_ensure_document()
            self.env["ff.fe.document"]._ff_schedule_cron()
        return res

    # ------------------------------------------------------------------
    # Contrato de fuente (ver ff.fe.source)
    # ------------------------------------------------------------------
    def _ff_doc_vals(self):
        vals = super()._ff_doc_vals()
        vals["pos_order_id"] = self.id
        return vals

    def _ff_vincular_factura(self):
        """Enseña en la factura el documento FE que emitió esta orden.

        El documento fiscal cuelga de la ORDEN, no de la factura: la venta ya se
        emitió al cobrar y la factura no se vuelve a emitir (sería un segundo
        documento ante la DGI). Pero quien mira Contabilidad necesita ver el
        estado igualmente — sin esto, con facturación automática del POS, todas
        las facturas aparecen sin estado FE.

        Se comparte el mismo registro, no se copia: un cambio de estado se
        refleja en los dos sitios a la vez."""
        self.ensure_one()
        factura = self.sudo().account_move
        doc = self.sudo().fe_document_id
        if factura and doc and factura.fe_document_id != doc:
            factura.fe_document_id = doc
        return factura

    def _ff_ensure_document(self):
        # La factura ya existe a estas alturas (_generate_pos_order_invoice corre
        # antes, dentro de _process_saved_order): se enlaza al crear el documento.
        doc = super()._ff_ensure_document()
        self._ff_vincular_factura()
        return doc

    def _generate_pos_order_invoice(self):
        # Y al reves: la factura puede nacer DESPUES del documento FE (una orden
        # del POS que se factura luego desde el backend).
        res = super()._generate_pos_order_invoice()
        for order in self:
            if order.fe_document_id:
                order._ff_vincular_factura()
        return res

    def _ff_emitible(self):
        """Solo las ventas cobradas se facturan (nunca un borrador, p.ej. una
        mesa abierta en restaurante)."""
        self.ensure_one()
        cfg = self.config_id
        return bool(cfg.ff_agent_enabled and cfg.ff_base_url
                    and self.state in ("paid", "done", "invoiced"))

    def _ff_no_emitible_msg(self):
        return _("Para firmar, la orden debe estar cobrada y su punto de venta "
                 "debe tener Factura Fácil activo con la URL configurada.")

    def _ff_agent_config(self):
        """Conexion con el agente para ff.fe.document: la del punto de venta."""
        cfg = self.config_id
        return {
            "enabled": bool(cfg.ff_agent_enabled),
            "mode": cfg.ff_mode or "agent",
            "base_url": cfg.ff_base_url,
            "company_uuid": cfg.ff_company_uuid,
            "api_key": cfg.ff_api_key,
            "env_name": cfg._ff_env_name(),
            "print_cafe": bool(cfg.ff_print_cafe),
        }

    def _ff_header_id(self):
        ref = self.pos_reference
        if ref and ref not in ("/", ""):
            return ref
        return self.name if (self.name and self.name != "/") else ("POS-%s" % self.id)

    def _ff_is_refund(self):
        """Una devolucion del POS se emite como nota de credito (NTC).

        Odoo guarda la devolucion como una orden con cantidades y totales en
        NEGATIVO; la DGI no acepta signos: el sentido lo da el tipo de
        documento y los montos viajan siempre en valor absoluto."""
        self.ensure_one()
        return bool(self.refunded_order_id) or (self.amount_total or 0.0) < 0.0

    def _ff_referred(self):
        """Documento afectado por la NC: CUFE de 66 chars (preferido) o el
        numero fiscal de 10 digitos, mas su fecha de emision (YYYY-MM-DD)."""
        self.ensure_one()
        origin = self.refunded_order_id
        if not origin:
            return None
        fd_number = origin.fe_cufe or origin.fe_nro_dgi
        if not fd_number:
            return None
        date = origin.date_order or self.date_order or fields.Datetime.now()
        return {
            "fd_number": fd_number,
            "fd_date": fields.Datetime.context_timestamp(self, date).date().isoformat(),
        }

    def _ff_partner(self):
        return self.partner_id

    def _ff_lines(self):
        return self.lines

    def _ff_line_qty(self, line):
        return line.qty

    def _ff_line_base(self, line):
        return line.price_subtotal

    def _ff_line_tax(self, line):
        return line.price_subtotal_incl - line.price_subtotal

    def _ff_line_label(self, line):
        return line.full_product_name or line.product_id.display_name

    def _ff_total(self):
        return self.amount_total

    def _ff_date(self):
        return self.date_order

    def _ff_pos_code(self):
        return self.config_id.ff_pos_code

    def _ff_branch_code(self):
        return self.config_id.ff_branch_code

    def _ff_environment(self):
        return self.config_id.ff_environment

    def _ff_gns_default(self):
        return self.config_id.ff_codigo_gns

    def _ff_credit_installments(self):
        return self.config_id.ff_credit_installments

    def _ff_info_tag(self):
        return "[D]OdooPOS=%s[/D]" % (self.pos_reference or self.name or "")

    def _ff_manager_group(self):
        return "point_of_sale.group_pos_manager"

    def _ff_build_payments(self):
        payments = []
        # Odoo registra el vuelto como un pago en efectivo negativo con
        # is_change=True. Con abs() se sumaria como OTRO pago: se excluye y se
        # manda lo entregado; el agente calcula el vuelto (suma - total).
        for pay in self.payment_ids.filtered(lambda p: not p.is_change):
            method = pay.payment_method_id
            ptype = method.ff_payment_type or method._ff_guess_payment_type()
            payment = {"type": ptype, "amount": round(abs(pay.amount), 2)}
            if ptype == "99":
                # 'Otro' exige una descripcion de 10 a 100 caracteres.
                desc = (method.name or "Otro").strip()
                payment["description"] = (desc if len(desc) >= 10 else "%s (pago POS)" % desc)[:100]
            payments.append(payment)
        if not payments:
            payments = [{"type": "02", "amount": round(abs(self.amount_total), 2)}]
        return payments

    def _ff_payment_names(self):
        """Para el CAFE: las formas de pago y el vuelto (is_change no es un
        pago: se muestra aparte, como VUELTO)."""
        pagos = [(p.payment_method_id.name or "", abs(p.amount))
                 for p in self.payment_ids.filtered(lambda p: not p.is_change)]
        vuelto = abs(sum(self.payment_ids.filtered("is_change").mapped("amount")))
        return pagos, vuelto


    # ------------------------------------------------------------------
    # Modo "agente en la caja": el navegador habla con SU agente local
    # ------------------------------------------------------------------
    # Reparto de responsabilidades, a proposito:
    #
    # - El PAYLOAD lo construye el servidor (esta clase + ff.fe.source), que es
    #   codigo probado. El navegador no calcula bases ni ITBMS: solo transporta.
    # - El TRANSPORTE lo hace el navegador, porque el agente esta en 127.0.0.1
    #   de esa caja y el servidor no lo alcanza.
    # - El ESTADO FISCAL lo escribe el servidor, validando lo que le devuelve
    #   la caja contra el payload que el mismo genero.
    #
    # Cuando la caja se queda sin internet no puede pedir el payload: la venta
    # queda encolada en el POS y se emite al reconectar (ver ff_local.js). Esa
    # es la limitacion conocida de esta etapa.

    def ff_local_prepare(self):
        """Lo que la caja necesita para emitir contra su agente local.

        Devuelve el payload ya construido y firmado por el servidor (queda en
        la auditoria del documento antes de salir) mas los datos de conexion.
        Si el documento ya tiene UUID no se vuelve a emitir NUNCA: el agente no
        deduplica y un segundo POST crearia otro documento fiscal."""
        self.ensure_one()
        cfg = self.config_id
        if not cfg._ff_is_client_mode():
            raise UserError(_("Este punto de venta no emite desde la caja."))
        if not self._ff_emitible():
            return {"skip": "no_emitible"}
        doc = self._ff_ensure_document().sudo()
        if doc.fe_document_uuid:
            # Ya existe en el agente: solo se consulta, jamas se reenvia.
            return {"skip": "ya_emitido", "document_uuid": doc.fe_document_uuid,
                    "doc_id": doc.id}
        if doc.fe_estado == "incierta":
            # Un envio quedo sin respuesta: reenviar podria duplicar la factura.
            return {"skip": "incierta", "doc_id": doc.id,
                    "message": doc.fe_error or ""}
        try:
            payload = self._ff_build_payload()
        except UserError as e:
            doc._ff_set({"fe_estado": "error", "fe_error": str(e)})
            return {"skip": "error", "message": str(e)}
        except Exception as e:  # noqa: BLE001
            # Cualquier otro fallo construyendo el documento NO debe volver a la
            # caja como un 500 opaco: el cajero tiene un cliente delante y lo
            # unico que veria es "Odoo Server Error".
            _logger.exception("[ff_pos_agent] no se pudo construir el payload de %s", self.id)
            doc._ff_set({"fe_estado": "error", "fe_error": str(e)})
            return {"skip": "error", "message": str(e)}
        # Auditoria antes de salir: si la caja muere a mitad, el payload consta.
        doc._ff_log_request(payload)
        # Marca de envio en curso: mientras no llegue el resultado, este
        # documento NO se puede volver a preparar (misma regla que el servidor).
        doc._ff_set({"fe_estado": "pendiente",
                     "fe_error": _("Enviando desde la caja al agente local...")})
        # sudo: las credenciales del agente llevan groups=responsable del POS,
        # pero es el CAJERO quien necesita mandarlas a su agente local. Sin esto
        # la preparacion revienta con AccessError para cualquiera que no sea
        # responsable — y llega a la caja como un 500 sin explicacion.
        agent = self.sudo()._ff_agent_config()
        _logger.info("[ff_pos_agent] caja %s: payload preparado para la orden %s (doc %s)",
                     cfg.name, self._ff_header_id(), doc.id)
        return {
            "doc_id": doc.id,
            "payload": payload,
            "base_url": (agent["base_url"] or "").rstrip("/"),
            "env_name": agent["env_name"],
            "company_uuid": agent["company_uuid"] or "",
            "api_key": agent["api_key"] or "",
            "header_id": self._ff_header_id(),
            # La impresion del CAFE la manda la caja a SU agente: es una llamada
            # aparte de la firma, contra la impresora de esa maquina.
            "print_cafe": bool(agent["print_cafe"]),
        }

    def ff_local_apply(self, result):
        """Aplica el resultado que la caja obtuvo de su agente local.

        `result` viene del navegador, asi que NO se cree a ciegas: solo se
        acepta para un documento que este servidor preparo (tiene payload en la
        auditoria) y que aun no tenga UUID. El estado fiscal lo escribe el
        servidor, no la caja."""
        self.ensure_one()
        doc = self.sudo().fe_document_id
        if not doc:
            raise UserError(_("Esta orden no tiene documento de factura electrónica."))
        result = result or {}
        _logger.info("[ff_pos_agent] caja: resultado '%s' para la orden %s (HTTP %s, uuid %s)",
                     result.get("estado"), self._ff_header_id(),
                     result.get("status"), result.get("document_uuid"))
        doc._ff_log_response(result.get("status") or "-",
                             result.get("body") or "", label="caja")
        estado = result.get("estado")
        if estado == "no_entregado":
            # La peticion no salio de la caja (agente apagado, DNS): reenviar
            # es seguro, el documento vuelve a quedar pendiente.
            doc._ff_set({"fe_estado": "pendiente",
                         "fe_error": result.get("message") or _("El agente no respondió.")})
        elif estado == "incierta":
            doc._ff_mark_incierta(result.get("message") or _("sin respuesta del agente"))
        elif estado == "error":
            doc._ff_set({"fe_estado": "error", "fe_error": result.get("message") or ""})
        elif result.get("document_uuid"):
            self._ff_verify_local_payload(result.get("payload"))
            # _ff_apply_signed escribe CUFE, QR y los datos del CAFE a partir
            # del rFE firmado: el mismo camino que usa el servidor.
            doc._ff_apply_signed(result.get("rfe"), result["document_uuid"])
            if result.get("resultado"):
                doc._ff_apply_result_data(result["resultado"])
            self._ff_apply_local_print(result.get("impresion"))
        else:
            doc._ff_mark_incierta(_("respuesta del agente sin document_uuid"))
        return self._ff_fe_data()

    def _ff_apply_local_print(self, impresion):
        """Registra si el agente de la caja imprimió el CAFE.

        Que no imprima no invalida nada: el documento fiscal ya está emitido y
        el CAFE puede salir con el recibo de Odoo. Solo queda en la auditoría."""
        self.ensure_one()
        if not impresion:
            return
        doc = self.sudo().fe_document_id
        doc._ff_log_response(impresion.get("status") or "-",
                             impresion.get("body") or impresion.get("message") or "",
                             label="printByCufe (caja)")
        if impresion.get("impreso"):
            doc._ff_set({"fe_impreso": True})

    def _ff_verify_local_payload(self, enviado):
        """Comprueba que la caja envió EXACTAMENTE el payload que se le dio.

        El servidor ya lo guardó en la auditoría antes de entregarlo; si lo que
        viajó no coincide, queda constancia en el log y en el documento. No se
        rechaza el resultado (el documento fiscal ya existe en la DGI: negarlo
        solo lo dejaría huérfano), pero deja rastro para poder anularlo."""
        self.ensure_one()
        doc = self.sudo().fe_document_id
        if not enviado or not doc.fe_request_json:
            return True
        try:
            esperado = json.loads(doc.fe_request_json)
        except ValueError:
            return True
        if isinstance(esperado, dict) and esperado.get("document") == (enviado or {}).get("document"):
            return True
        _logger.error(
            "[ff_pos_agent] la caja emitió un payload distinto al preparado "
            "por el servidor en la orden %s (id=%s)", self.name, self.id)
        doc._ff_log_append(
            "=== AVISO: el documento emitido desde la caja NO coincide con el "
            "payload preparado por el servidor ===")
        return False

    def ff_fe_data(self):
        """Los campos FE que consume el recibo del POS.

        Publico a proposito: lo llama la caja por RPC, y Odoo no permite invocar
        remotamente metodos que empiezan por guion bajo."""
        self.ensure_one()
        return self._ff_fe_data()

    def ff_local_print_done(self, impresion):
        """La caja informa de que su agente imprimió (o no) el CAFE.

        Publico por lo mismo. No toca el documento fiscal: la impresora es un
        periferico, su resultado solo se registra."""
        self.ensure_one()
        self._ff_apply_local_print(impresion)
        return True

    def _ff_fe_data(self):
        """Los campos FE que consume el recibo del POS."""
        self.ensure_one()
        data = {f: self[f] for f in (
            "fe_estado", "fe_cufe", "fe_nro_dgi", "fe_protocolo", "fe_error",
            "fe_qr_base64", "fe_qr_url", "fe_confirmacion", "fe_doc_json",
            # El recibo lo necesita: si el agente ya imprimio el CAFE, llevar
            # ademas al cajero al PDF de Odoo es un paso de mas.
            "fe_impreso")}
        data["fe_cafe_url"] = self._ff_cafe_url()
        return data

    # ------------------------------------------------------------------
    # Accion propia del POS
    # ------------------------------------------------------------------
    def ff_emitir_data(self):
        """Llamado desde el POS (JS), en su propia transaccion: justo despues de
        sincronizar la venta (para imprimir el CAFE) y desde el boton
        Reintentar. Emite o retoma el documento y devuelve los datos FE."""
        self.ensure_one()
        # sudo: el cajero solo LEE documentos FE (no puede escribir un estado
        # fiscal por RPC); el documento lo actualiza el propio flujo de emision.
        if self.fe_document_id:
            self.fe_document_id.sudo()._ff_retry(wait=True)
        # Lo que usa el recibo (ver pos_order_patch.js), incluido el CAFE.
        return self._ff_fe_data()
