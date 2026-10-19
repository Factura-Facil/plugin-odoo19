"""Fuente de un documento FE: lo comun a `pos.order` y `account.move`.

`ff.fe.document` es el ciclo de vida del envio (estado, CUFE, HTTP, cron) y le
pide a su fuente solo tres cosas: `_ff_agent_config()`, `_ff_build_payload()` y
`_ff_cafe_payload(rfe)`. Este mixin las construye una sola vez para las dos
fuentes: la aritmetica fiscal (bases, ITBMS, reparto de descuentos de signo
contrario, cuotas de credito) es identica y un fix aqui vale para ambas.

Lo que cambia de un modelo a otro son accesos pequenos, no la logica: como se
llama la cantidad de la linea (`qty` / `quantity`), de donde sale el ITBMS, que
config lleva las credenciales. Eso es lo que cada fuente implementa abajo en
"contrato".
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# ITBMS: porcentaje -> codigo DGI (00 exento, 01 7%, 02 10%, 03 15%)
ITBMS_CODE_BY_PCT = {0: "00", 7: "01", 10: "02", 15: "03"}
# Vencimiento de lo vendido a credito si el cliente no tiene plazo de pago.
FF_CREDIT_DAYS = 30


class FfFeSource(models.AbstractModel):
    _name = "ff.fe.source"
    _description = "Fuente de un documento FE (Factura Fácil)"

    fe_document_id = fields.Many2one("ff.fe.document", "Documento FE", copy=False,
                                     index="btree_not_null", readonly=True)
    # Espejo del documento: las vistas y el Monitor leen estos campos de la
    # fuente. Guardados para poder filtrar y agrupar por estado.
    fe_estado = fields.Selection(related="fe_document_id.fe_estado", store=True, index=True)
    fe_cufe = fields.Char(related="fe_document_id.fe_cufe", store=True)
    fe_nro_dgi = fields.Char(related="fe_document_id.fe_nro_dgi", store=True)
    fe_protocolo = fields.Char(related="fe_document_id.fe_protocolo", store=True)
    fe_confirmacion = fields.Char(related="fe_document_id.fe_confirmacion", store=True)
    fe_error = fields.Text(related="fe_document_id.fe_error", store=True)
    fe_qr_base64 = fields.Text(related="fe_document_id.fe_qr_base64", store=True)
    fe_qr_url = fields.Char(related="fe_document_id.fe_qr_url", store=True)
    # Editable: el supervisor vincula el documento que encontro en el agente
    # cuando el envio quedo incierto (ver la vista).
    fe_document_uuid = fields.Char(related="fe_document_id.fe_document_uuid", store=True,
                                   readonly=False)
    fe_doc_json = fields.Text(related="fe_document_id.fe_doc_json", store=True)
    fe_request_json = fields.Text(related="fe_document_id.fe_request_json")
    fe_response_json = fields.Text(related="fe_document_id.fe_response_json")
    fe_request_date = fields.Datetime(related="fe_document_id.fe_request_date")
    fe_impreso = fields.Boolean(related="fe_document_id.fe_impreso")
    fe_cafe_pdf_id = fields.Many2one(related="fe_document_id.fe_cafe_pdf_id")
    # True cuando el documento es emitible y aun NO tiene documento FE: es
    # cuando tiene sentido el boton "Firmar" del backend.
    ff_puede_firmar = fields.Boolean(compute="_compute_ff_puede_firmar")

    def _compute_ff_puede_firmar(self):
        # Cada fuente redeclara el metodo con su @api.depends (los campos de
        # los que depende son suyos); el cuerpo es este.
        for rec in self:
            rec.ff_puede_firmar = rec._ff_emitible() and not rec.fe_document_id

    # ==================================================================
    # Contrato: lo que implementa cada fuente
    # ==================================================================
    def _ff_agent_config(self):
        """dict con la conexion al agente (url, credenciales, modo, ambiente)."""
        raise NotImplementedError

    def _ff_emitible(self):
        """True si el documento esta en un estado en que se puede facturar."""
        raise NotImplementedError

    def _ff_header_id(self):
        """`header.id`: la referencia del documento en el agente."""
        raise NotImplementedError

    def _ff_is_refund(self):
        """True si se emite como nota de credito (NTC)."""
        raise NotImplementedError

    def _ff_referred(self):
        """{fd_number, fd_date} del documento afectado por la NC, o None."""
        raise NotImplementedError

    def _ff_partner(self):
        raise NotImplementedError

    def _ff_lines(self):
        """Las lineas a facturar (ya sin secciones ni notas)."""
        raise NotImplementedError

    def _ff_line_qty(self, line):
        raise NotImplementedError

    def _ff_line_base(self, line):
        """Base imponible de la linea, SIN ITBMS y con su signo original."""
        raise NotImplementedError

    def _ff_line_tax(self, line):
        """ITBMS de la linea, con su signo original."""
        raise NotImplementedError

    def _ff_line_label(self, line):
        raise NotImplementedError

    def _ff_build_payments(self):
        """[{type, amount, description?}] con las formas de pago DGI."""
        raise NotImplementedError

    def _ff_payment_names(self):
        """[(nombre, monto)] para el CAFE, y el vuelto."""
        raise NotImplementedError

    def _ff_total(self):
        raise NotImplementedError

    def _ff_date(self):
        raise NotImplementedError

    def _ff_pos_code(self):
        raise NotImplementedError

    def _ff_branch_code(self):
        raise NotImplementedError

    def _ff_environment(self):
        raise NotImplementedError

    def _ff_gns_default(self):
        raise NotImplementedError

    def _ff_credit_installments(self):
        raise NotImplementedError

    def _ff_info_tag(self):
        """Marca de origen que viaja en `document.info`."""
        raise NotImplementedError

    def _ff_manager_group(self):
        """Grupo que puede forzar un reenvio incierto."""
        raise NotImplementedError

    # ==================================================================
    # Documento FE
    # ==================================================================
    def _ff_doc_vals(self):
        """Campos con los que se crea el ff.fe.document de esta fuente."""
        self.ensure_one()
        return {"name": self._ff_header_id(), "company_id": self.company_id.id}

    def _ff_ensure_document(self):
        """Crea el ff.fe.document de esta fuente si aun no existe y lo devuelve."""
        self.ensure_one()
        if not self.fe_document_id:
            self.sudo().fe_document_id = self.env["ff.fe.document"].sudo().create(
                self._ff_doc_vals())
        return self.fe_document_id

    # ==================================================================
    # Construccion del documento DGI
    # ==================================================================
    def _ff_build_payload(self):
        self.ensure_one()
        is_refund = self._ff_is_refund()
        receptor = self._ff_build_receptor()
        payments = self._ff_build_payments()
        payment_term, installments = self._ff_payment_terms(payments)
        document = {
            "fd_number": 0,
            "type": "NTC" if is_refund else "FAC",
            # En NC/ND el tipo de venta va nulo EXPLICITO: omitirlo aplica el
            # default "1" y emite <iTipoTranVenta> indebidamente.
            "sale_type": None if is_refund else "1",
            "payment_term": payment_term,
            "receptor": receptor,
            "items": self._ff_build_items(receptor.get("type")),
            "payments": payments,
            "info": self._ff_info_tag(),
        }
        if installments:
            document["installments"] = installments
        if is_refund:
            document["referred"] = self._ff_referred()
            if not document["referred"]:
                raise UserError(_("Nota de crédito sin documento de origen timbrado: el "
                                  "documento rectificado no tiene CUFE ni número fiscal, "
                                  "la DGI la rechazaría."))
        return {
            "header": {
                "id": self._ff_header_id(),
                "environment": self._ff_environment() or "2",
                "pos": self._ff_pos_code() or "001",
                "branch": {"code": self._ff_branch_code() or "0000"},
            },
            "document": document,
        }

    def _ff_build_receptor(self):
        p = self._ff_partner()
        if not p:
            return {"type": "02", "name": "CONSUMIDOR FINAL"}
        rtype = p.ff_receptor_type or "02"
        # Contribuyente / Gobierno: requiere RUC + DV + direccion + ubicacion.
        # Solo se emite asi si el cliente tiene RUC; si no, se degrada a
        # consumidor final personalizado (con su nombre).
        if rtype in ("01", "03") and p.ff_ruc:
            return {
                "type": rtype,
                "name": (p.ff_razon_social or p.name or "")[:100],
                "ruc": p.ff_ruc,
                "ruc_type": p.ff_ruc_type or "2",
                "dv": p.ff_dv or "",
                # La direccion es obligatoria para 01/03; default "Panama".
                "address": (p.street or p.city or "Panama")[:100],
                # La ubicacion tambien; default corregimiento de Panama (8-8-7).
                "location": p.ff_location_code or "8-8-7",
                **({"email": p.email} if p.email else {}),
            }
        # Consumidor final: personalizado con el nombre del cliente si lo tiene.
        return {"type": "02", "name": (p.name or "CONSUMIDOR FINAL")[:100]}

    def _ff_gns(self, line, receptor_type):
        """Codigo CPBS del item. La DGI lo exige cuando el receptor es Gobierno
        (03); se toma el del articulo y, si no tiene, el default de la fuente."""
        code = line.product_id.ff_gns_code
        if not code and receptor_type == "03":
            code = self._ff_gns_default()
        return (code or "").strip()[:4]

    def _ff_fiscal_lines(self):
        """Las lineas tal como se emiten a la DGI (payload y CAFE usan esto).

        El agente totaliza `(price - discount) * qty + itbms` con `price` SIN
        impuestos y `discount` POR UNIDAD. Cada linea devuelve esos valores en
        absoluto (el sentido lo da el tipo FAC/NTC), sacados de los montos que
        Odoo ya calculo, para que el total emitido sea el que se cobro:

        - base: sin ITBMS aunque el precio lo incluya.
        - Las lineas de signo contrario al documento (descuento global,
          premios de lealtad, lineas negativas) no son items: se reparten como
          descuento entre los items con la misma tasa de ITBMS, proporcional a
          su base.

        En una devolucion los montos viajan en valor absoluto (el sentido lo da
        el tipo NTC), asi que la fuente entrega la base con el signo del
        documento y aqui se invierte.
        """
        self.ensure_one()
        sign = -1.0 if self._ff_is_refund() else 1.0
        items, contra = [], []
        for line in self._ff_lines():
            qty_raw = self._ff_line_qty(line)
            base = self._ff_line_base(line) * sign
            tax = self._ff_line_tax(line) * sign
            pct = int(round(sum(t.amount for t in line.tax_ids if t.amount_type == "percent")))
            # Coherencia tasa<->monto (evita el rechazo 2152): si no hay
            # impuesto efectivo, la tasa DEBE ser 00 (exento).
            if round(tax, 2) == 0.0:
                pct, tax = 0, 0.0
            elif pct not in ITBMS_CODE_BY_PCT:
                # La DGI solo tiene ITBMS 7/10/15%: otra tasa se rechazaria
                # (y mandarla como exenta con monto seria peor).
                raise UserError(_(
                    "'%(linea)s' tiene un impuesto de %(pct)s%%, que no es una tasa de "
                    "ITBMS (7, 10 o 15%%). Corrige el impuesto del producto.",
                    linea=self._ff_line_label(line), pct=pct))
            if base < 0 or (base == 0 and (line.price_unit or 0.0) * (qty_raw or 0.0) * sign < 0):
                contra.append({"line": line, "base": -base, "tax": -tax, "pct": pct})
                continue
            qty = abs(qty_raw or 0.0)
            disc_pct = line.discount or 0.0
            if qty and disc_pct < 100.0:
                gross = base / qty / (1.0 - disc_pct / 100.0)
            else:
                # 100% de descuento: base 0, el precio sale del precio de lista.
                gross = line.tax_ids.compute_all(
                    abs(line.price_unit or 0.0), self._ff_currency(), 1.0,
                    product=line.product_id)["total_excluded"]
            items.append({
                "line": line, "qty": qty, "price": gross,
                "discount": (gross - base / qty) if qty else 0.0,
                "base": base, "tax": tax, "pct": pct,
            })
        for c in contra:
            grupo = [it for it in items if it["pct"] == c["pct"] and it["base"] > 0]
            total_base = sum(it["base"] for it in grupo)
            if c["base"] > total_base + 0.005:
                raise UserError(_(
                    "No se puede emitir: '%(linea)s' descuenta %(monto).2f pero las ventas "
                    "con ITBMS %(pct)s%% suman %(total).2f.",
                    linea=self._ff_line_label(c["line"]),
                    monto=c["base"], pct=c["pct"], total=total_base))
            resto_base, resto_tax = c["base"], c["tax"]
            for i, it in enumerate(grupo):
                if i == len(grupo) - 1:  # el ultimo absorbe el redondeo
                    b, t = resto_base, resto_tax
                else:
                    b = round(c["base"] * it["base"] / total_base, 6)
                    t = round(c["tax"] * it["base"] / total_base, 6)
                resto_base -= b
                resto_tax -= t
                it["base"] -= b
                it["tax"] -= t
                it["discount"] += b / it["qty"] if it["qty"] else 0.0
        for it in items:
            it["discount"] = min(max(it["discount"], 0.0), it["price"])
            it["tax"] = max(it["tax"], 0.0)
        return items

    def _ff_currency(self):
        self.ensure_one()
        return self.currency_id

    def _ff_build_items(self, receptor_type=None):
        items = []
        for seq, fl in enumerate(self._ff_fiscal_lines(), start=1):
            line = fl["line"]
            item = {
                "line": seq,
                "description": (self._ff_line_label(line) or "")[:500],
                # La DGI valida la unidad contra su catalogo; el nombre de UoM de
                # Odoo (traducido, ej. "Unidades") NO existe alli y provoca el
                # rechazo 2002: va el codigo DGI de la unidad, o "und".
                "mu": (line.product_uom_id.ff_dgi_code or "").strip() or "und",
                "quantity": "%.6f" % fl["qty"],
                "price": "%.6f" % fl["price"],
                "discount": "%.6f" % fl["discount"],
                "taxes": [{"type": "01", "code": ITBMS_CODE_BY_PCT.get(fl["pct"], "00"),
                           "amount": "%.6f" % fl["tax"]}],
            }
            gns = self._ff_gns(line, receptor_type)
            if gns:
                item["gns"] = gns
            if line.product_id.default_code:
                item["internal_code"] = line.product_id.default_code[:20]
            items.append(item)
        return items

    def _ff_payment_terms(self, payments):
        """(payment_term, installments). Lo cobrado a credito hace la venta a
        plazo (2) o mixta (3), y la DGI exige las cuotas: una sola, con el
        vencimiento del plazo de pago del cliente."""
        credito = round(sum(p["amount"] for p in payments if p["type"] == "01"), 2)
        # Sin la opcion, el credito viaja como forma de pago al contado: el
        # agente actual revienta con cuotas (ver ff_credit_installments).
        if not credito or not self._ff_credit_installments():
            return 1, None
        term = 2 if all(p["type"] == "01" for p in payments) else 3
        fecha = fields.Datetime.context_timestamp(
            self, self._ff_date() or fields.Datetime.now()).date()
        vence = fecha
        pterm = self._ff_partner().property_payment_term_id if self._ff_partner() else None
        if pterm:
            vence = max([fecha] + [line._get_due_date(fecha) for line in pterm.line_ids])
        if vence == fecha:
            vence = fecha + timedelta(days=FF_CREDIT_DAYS)
        return term, [{"expiration_date": vence.isoformat(), "value": "%.2f" % credito}]

    # ==================================================================
    # CAFE (comprobante auxiliar impreso)
    # ==================================================================
    def _ff_cafe_payload(self, rfe):
        """Datos compactos (ya formateados) para renderizar el CAFE: emisor
        (del XML firmado) + items/totales/pagos (del documento). Los montos van
        como texto 'B/.' de 2 decimales."""
        def m(v):
            return "B/.%0.2f" % (v or 0.0)

        gdgen = rfe.get("gDGen") or {}
        gemis = gdgen.get("gEmis") or {}
        ruc = (gemis.get("gRucEmi") or {})
        grec = gdgen.get("gDatRec") or {}
        is_refund = self._ff_is_refund()
        items, subtotal, tot_dscto = [], 0.0, 0.0
        itbms_por_tasa = {}
        # Las mismas lineas que el documento fiscal: montos en absoluto (el
        # sentido lo indica el titulo del CAFE), precio sin ITBMS y descuentos
        # globales ya repartidos. Asi el comprobante muestra lo que se emitio.
        for fl in self._ff_fiscal_lines():
            line, qty, price, pct = fl["line"], fl["qty"], fl["price"], fl["pct"]
            unit_disc = fl["discount"]
            gross = price * qty
            disc_total = unit_disc * qty
            line_tax = fl["tax"]
            subtotal += gross
            tot_dscto += disc_total
            itbms_por_tasa[pct] = itbms_por_tasa.get(pct, 0.0) + line_tax
            items.append({
                "desc": self._ff_line_label(line) or "",
                "qty_x_price": "%g X %0.2f (%d%%)" % (qty, price, pct),
                "gross": m(gross),
                "has_desc": round(disc_total, 2) > 0.0,
                "desc_line": "*DESCUENTO* %g X %0.2f" % (qty, unit_disc),
                "desc_total": "-" + m(disc_total),
            })
        itbms = [{"tasa": k, "val": m(v)} for k, v in sorted(itbms_por_tasa.items()) if k]
        pagos_raw, vuelto = self._ff_payment_names()
        pagos = [{"forma": nombre, "monto": m(monto)} for nombre, monto in pagos_raw] \
            or [{"forma": "Efectivo", "monto": m(abs(self._ff_total()))}]
        # Fecha: "2026-09-21T09:28:58-05:00" -> "21/09/2026 09:28"
        fecha = gdgen.get("dFechaEm") or ""
        try:
            dpart, tpart = fecha.split("T")
            yy, mm, dd = dpart.split("-")
            fecha = "%s/%s/%s %s" % (dd, mm, yy, tpart[:5])
        except Exception:  # noqa: BLE001
            pass
        return {
            "tipo": "NTC" if is_refund else "FAC",
            "titulo": ("COMPROBANTE AUXILIAR DE NOTA DE CRÉDITO ELECTRÓNICA"
                       if is_refund else "COMPROBANTE AUXILIAR DE FACTURA ELECTRÓNICA"),
            "subtitulo": "Nota de Crédito" if is_refund else "Factura de Operación Interna",
            "referred": (self._ff_referred() or {}) if is_refund else {},
            "emisor": {
                "ruc": ruc.get("dRuc") or "", "dv": ruc.get("dDV") or "",
                "nombre": gemis.get("dNombEm") or "",
                "dir": gemis.get("dDirecEm") or "",
                "tel": gemis.get("dTfnEm") or "",
                "sucursal": gemis.get("dSucEm") or (self._ff_branch_code() or ""),
            },
            "nro": gdgen.get("dNroDF") or self.fe_nro_dgi or "",
            "fecha": fecha,
            "pos": gdgen.get("dPtoFacDF") or (self._ff_pos_code() or ""),
            "ambiente": str(gdgen.get("iAmb") or self._ff_environment() or ""),
            "systemref": self._ff_header_id(),
            "receptor": {
                "tipo": grec.get("iTipoRec") or "02",
                "nombre": grec.get("dNombRec") or "CONSUMIDOR FINAL",
                "ruc": grec.get("dRucRec") or "",
            },
            "items": items,
            "tot": {
                "subtotal": m(subtotal),
                "dscto": "-" + m(tot_dscto),
                "has_dscto": tot_dscto > 0.0,
                "itbms": itbms,
                "total": m(abs(self._ff_total())),
            },
            "pagos": pagos,
            "vuelto": m(vuelto) if round(vuelto, 2) else "",
        }

    # ==================================================================
    # Acciones (comunes a las dos fuentes): delegan en el documento FE
    # ==================================================================
    def _ff_cafe_url(self):
        pdf = self.sudo().fe_document_id.fe_cafe_pdf_id
        return "/web/content/%s?download=false" % pdf.id if pdf else False

    def action_ff_ver_cafe(self):
        self.ensure_one()
        doc = self.fe_document_id.sudo()
        if doc and not doc.fe_cafe_pdf_id and doc.fe_cufe:
            doc._ff_maybe_print()  # la descarga habia fallado: se reintenta
        url = self._ff_cafe_url()
        if not url:
            raise UserError(_("Este documento no tiene el PDF oficial del CAFE."))
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_ff_firmar(self):
        """Boton del backend: firma/emite el documento ante la DGI. Crea el
        ff.fe.document si falta, emite en el momento y refresca. Para uno que ya
        tiene documento se usa 'Reintentar' (action_ff_retimbrar)."""
        self.ensure_one()
        if not self._ff_emitible():
            raise UserError(self._ff_no_emitible_msg())
        doc = self._ff_ensure_document()
        doc.sudo()._ff_retry(wait=True)
        return doc._ff_notify_estado()

    def _ff_no_emitible_msg(self):
        return _("Este documento todavía no se puede firmar ante la DGI.")

    def action_ff_retimbrar(self):
        """Boton del backend: reintenta, notifica el resultado y refresca."""
        self.ensure_one()
        if not self.fe_document_id:
            raise UserError(_("Este documento no tiene documento de factura electrónica."))
        self.fe_document_id.sudo()._ff_retry()
        return self.fe_document_id._ff_notify_estado()

    def action_ff_forzar_reenvio(self):
        self.ensure_one()
        if not self.fe_document_id:
            raise UserError(_("Este documento no tiene documento de factura electrónica."))
        # sudo no cambia el usuario: _ff_force_resend sigue exigiendo el grupo.
        self.fe_document_id.sudo()._ff_force_resend()
        return self.fe_document_id._ff_notify_estado()
