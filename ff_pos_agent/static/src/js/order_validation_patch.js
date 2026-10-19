/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { ffEmitirEnCaja } from "./local/ff_local";
import { ffLog } from "./local/ff_log";

// En Odoo 19 la validacion del pago se movio de PaymentScreen a la clase
// OrderPaymentValidation. El antiguo hook `_postPushOrderResolve` de PaymentScreen
// se reemplaza por `beforePostPushOrderResolve` de esta clase, que corre tras
// sincronizar la orden en el servidor (la sincronizacion no habla con el agente).
patch(OrderPaymentValidation.prototype, {
    /**
     * Si el agente de la caja ya imprimio el CAFE, no hay que mandar ademas el
     * recibo a la impresora de Odoo: para el cajero es un segundo paso que no
     * aporta nada (y en muchos equipos abre el dialogo del PDF).
     *
     * Se mira `fe_impreso`, no la configuracion: si el agente fallo al imprimir
     * SI queremos el recibo de Odoo, que es lo unico que le quedaria al cliente.
     */
    /**
     * Odoo descarga sola la factura en PDF de toda orden facturada
     * (`order_payment_validation.js`), y con un modulo de facturacion
     * automatica eso es TODAS. En la caja abre el explorador/descarga encima
     * del POS, y si el agente ya saco el CAFE por su impresora, el cajero
     * acaba con dos papeles y un paso de mas.
     *
     * Misma regla que el recibo: se mira `fe_impreso`, no la configuracion. Si
     * el agente no llego a imprimir, la descarga sigue saliendo — algo tiene
     * que llevarse el cliente. Y queda el boton "Ver factura (PDF)" del recibo.
     */
    shouldDownloadInvoice() {
        // OJO con el orden: en `finalizeValidation` la descarga de la factura
        // esta en la linea 163 y `postPushOrderResolve` (donde emitimos) en la
        // 193. Este gancho corre ANTES de la emision, asi que aqui `fe_impreso`
        // todavia es false SIEMPRE — mirarlo no servia de nada.
        //
        // Se decide por configuracion: si esta caja tiene el agente imprimiendo
        // el CAFE, no se descarga la factura sola. La red de seguridad va
        // despues: si el agente no llego a imprimir, la descarga se dispara al
        // terminar la emision (ver beforePostPushOrderResolve).
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

    get canPrintReceipt() {
        if (this.pos.config.ff_print_cafe && this.order?.fe_impreso) {
            return false;
        }
        return super.canPrintReceipt;
    },

    /**
     * La venta ya esta guardada en el servidor. Aqui se pide la emision FE, en
     * su propia transaccion, y se espera el resultado para que el recibo salga
     * con CUFE y QR. Un fallo NO tumba la venta: el recibo sale como pendiente,
     * con el boton Reintentar, y el cron la emite si nadie lo hace.
     */
    async beforePostPushOrderResolve(order, order_server_ids) {
        const result = await super.beforePostPushOrderResolve(...arguments);
        // Traza de entrada: si esto no sale, el problema es el gancho, no la FE.
        ffLog("Venta sincronizada — ¿emitir FE?", {
            ff_activo: Boolean(this.pos.config.ff_agent_enabled),
            modo: this.pos.config.ff_mode,
            orden_id: order?.id || null,
        });
        if (this.pos.config.ff_agent_enabled && order?.id) {
            const ui = this.pos.env?.services?.ui;
            ui?.block?.();
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
                console.warn("[ff_pos_agent] emisión FE falló:", e);
            } finally {
                ui?.unblock?.();
            }
        }
        return result;
    },
});
