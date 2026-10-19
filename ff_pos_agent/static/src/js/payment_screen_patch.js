/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ffEmitirEnCaja } from "./local/ff_local";
import { ffLog } from "./local/ff_log";

patch(PaymentScreen.prototype, {
    /**
     * Odoo descarga sola la factura en PDF de toda orden facturada, y con un
     * modulo de facturacion automatica eso es TODAS. En la caja abre el
     * explorador encima del POS; si el agente saca el CAFE por su impresora, el
     * cajero acaba con dos papeles.
     *
     * OJO con el orden: este gancho corre ANTES de la emision (en Odoo 19, que
     * es donde se verifico, la descarga esta en finalizeValidation:163 y la
     * emision en la 193), asi que aqui `fe_impreso` todavia es false SIEMPRE.
     * Se decide por configuracion, que es lo unico que se conoce a esta altura.
     * La red de seguridad va detras: si el agente no llego a imprimir, la
     * descarga se dispara al terminar la emision.
     *
     * En 18 no se ha podido verificar que PaymentScreen exponga este metodo; si
     * la supresion no surte efecto, es aqui donde hay que mirar.
     */
    shouldDownloadInvoice() {
        if (this.pos.config.ff_agent_enabled && this.pos.config.ff_print_cafe) {
            return false;
        }
        return super.shouldDownloadInvoice();
    },

    /**
     * Descarga la factura solo si el agente NO imprimio el CAFE. Nunca rompe la
     * venta: si falla, el recibo sigue teniendo el boton "Ver factura de Odoo".
     */
    async _ffDescargarFacturaSiNoImprimio(order) {
        if (!this.pos.config.ff_print_cafe || order?.fe_impreso) {
            return;
        }
        const moveId = order?.raw?.account_move || order?.account_move;
        if (!moveId || !order.isToInvoice?.()) {
            return;
        }
        try {
            ffLog("El agente no imprimió: se descarga la factura de Odoo", { move_id: moveId });
            await this.pos.env.services.account_move.downloadPdf(moveId);
        } catch (e) {
            console.warn("[ff_pos_agent] no se pudo descargar la factura:", e);
        }
    },

    /**
     * Si el agente ya imprimio el CAFE, no se manda ademas el recibo a la
     * impresora de Odoo. Aqui `fe_impreso` SI esta puesto: este gancho corre
     * despues de la emision.
     */
    get canPrintReceipt() {
        const order = this.currentOrder || this.pos.getOrder?.();
        if (this.pos.config.ff_print_cafe && order?.fe_impreso) {
            return false;
        }
        return super.canPrintReceipt;
    },

    async _postPushOrderResolve(order, order_server_ids) {
        const result = await super._postPushOrderResolve(...arguments);
        if (this.pos.config.ff_agent_enabled && order.id) {
            this.env.services.ui.block();
            try {
                if (this.pos.config.ff_mode === "agent_local") {
                    // El agente vive en esta caja: lo llama el navegador.
                    await ffEmitirEnCaja(this.pos, order);
                    // La descarga de la factura se suprimio antes de emitir. Si
                    // el agente no llego a imprimir, el cliente se quedaria sin
                    // ningun papel: se dispara ahora.
                    await this._ffDescargarFacturaSiNoImprimio(order);
                } else {
                    const rec = await this.pos.data.call("pos.order", "ff_emitir_data", [
                        [order.id],
                    ]);
                    order.ffApplyFe(rec);
                }
            } catch (e) {
                // No es un fallo de la venta: el recibo sale como pendiente,
                // con el boton Reintentar, y el cron la emite si nadie lo hace.
                console.warn("[ff_pos_agent] emisión FE falló:", e);
            } finally {
                this.env.services.ui.unblock();
            }
        }
        return result;
    },
});
