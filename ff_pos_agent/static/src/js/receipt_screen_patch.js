/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { ffEmitirEnCaja, ffImprimirCafe } from "./local/ff_local";

patch(ReceiptScreen.prototype, {
    /**
     * Accesos seguros para la plantilla.
     *
     * `fe_impreso` es un related NO almacenado y `raw` no siempre existe en el
     * modelo cliente: leerlos directo desde el QWeb puede lanzar, y una
     * excepcion renderizando mata el componente raiz de Owl — pantalla en
     * blanco con la venta ya cobrada y timbrada. Aqui se leen con red.
     */
    get ffImpreso() {
        try {
            return Boolean(this.pos.getOrder()?.fe_impreso);
        } catch {
            return false;
        }
    },

    get ffFacturaId() {
        try {
            const order = this.pos.getOrder();
            return order?.raw?.account_move || order?.account_move || false;
        } catch {
            return false;
        }
    },

    /** Reimprime el CAFE en la impresora del agente de esta caja. */
    async reimprimirCafe() {
        const order = this.pos.getOrder();
        const res = await ffImprimirCafe(this.pos, order);
        this.notification.add(
            res.impreso
                ? "CAFE enviado a la impresora del agente."
                : "No se pudo imprimir en el agente: " + (res.message || "revisa la impresora"),
            { type: res.impreso ? "success" : "danger", sticky: !res.impreso }
        );
    },

    /** La factura de Odoo en PDF, cuando la descarga automática se suprimió. */
    async verFacturaPdf() {
        const order = this.pos.getOrder();
        const moveId = this.ffFacturaId;
        if (!moveId) {
            this.notification.add("Esta orden no tiene factura de Odoo.", { type: "warning" });
            return;
        }
        await this.pos.env.services.account_move.downloadPdf(moveId);
    },

    /** El recibo con el diseño de Odoo, a la impresora del navegador. */
    async imprimirReciboOdoo() {
        await this.pos.printReceipt({ order: this.pos.getOrder() });
    },

    /**
     * Reintenta el timbrado de una orden que quedo "pendiente"/"error"
     * (agente caido al validar). Llama al metodo server-side, que hace la
     * llamada al agente, y reimprime el recibo ya con CUFE/QR.
     */
    async retryFe() {
        const order = this.pos.getOrder(); // Odoo 19: get_order() -> getOrder()
        if (!order.id) {
            this.notification.add("La orden aún no se ha sincronizado.", { type: "warning" });
            return;
        }
        let rec;
        try {
            rec =
                this.pos.config.ff_mode === "agent_local"
                    ? await ffEmitirEnCaja(this.pos, order)
                    : await this.pos.data.call("pos.order", "ff_emitir_data", [[order.id]]);
        } catch (e) {
            console.warn("[ff_pos_agent] retimbrado falló:", e);
            // UserError del servidor (p.ej. otro proceso ya esta enviando la orden)
            this.notification.add(e?.data?.message || "Agente FE no disponible - reintenta más tarde", {
                type: "warning",
            });
            return;
        }
        if (!rec) {
            this.notification.add("No se pudo emitir contra el agente de esta caja.", {
                type: "danger",
            });
            return;
        }
        order.ffApplyFe(rec);
        if (rec.fe_estado === "timbrada" || rec.fe_estado === "autorizada") {
            this.notification.add("Factura timbrada correctamente", { type: "success" });
            await this.pos.printReceipt({ order }); // Odoo 19: printReceipt recibe {order}
        } else {
            this.notification.add("No se pudo timbrar: " + (rec.fe_error || ""), {
                type: "danger",
            });
        }
    },
});
