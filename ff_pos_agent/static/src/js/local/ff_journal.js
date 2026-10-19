/** @odoo-module **/
/**
 * Diario de envios al agente local. Es la pieza que impide duplicar un
 * documento fiscal.
 *
 * El agente NO deduplica: reenviar el mismo payload crea OTRO documento con
 * otro CUFE ante la DGI. En el modo servidor esa garantia la da el estado
 * guardado en la base (con commit antes del POST). Cuando quien emite es el
 * navegador no hay transaccion que valga, asi que la marca se escribe en
 * localStorage ANTES de salir y solo se borra cuando el servidor confirma que
 * registro el resultado.
 *
 * Estados:
 *   enviando  el POST salio (o pudo salir) y no sabemos el resultado
 *             -> NUNCA se reenvia solo; se reporta como envio incierto
 *   enviado   hay document_uuid; falta que el servidor lo registre
 *             -> se puede reintentar la CONSULTA, nunca el POST
 *
 * Sobrevive a recargas, cierres del navegador y caidas de la caja, que es
 * justo cuando se duplican las facturas.
 */

const CLAVE = "ff_pos_agent.envios";
// Un envio sin resolver caduca a los 7 dias: para entonces ya se resolvio a
// mano en el backend y mantenerlo solo estorba.
const CADUCIDAD_MS = 7 * 24 * 60 * 60 * 1000;

function leer() {
    try {
        return JSON.parse(localStorage.getItem(CLAVE) || "{}");
    } catch {
        return {};
    }
}

function escribir(datos) {
    try {
        localStorage.setItem(CLAVE, JSON.stringify(datos));
        return true;
    } catch {
        // Sin localStorage (modo privado, cuota) no hay garantia de
        // idempotencia: quien llama debe abortar el envio.
        return false;
    }
}

export const ffJournal = {
    /** Entrada viva de una orden, o null. */
    get(headerId) {
        const datos = leer();
        const e = datos[headerId];
        if (!e) {
            return null;
        }
        if (Date.now() - (e.ts || 0) > CADUCIDAD_MS) {
            this.clear(headerId);
            return null;
        }
        return e;
    },

    /**
     * Marca que se va a enviar. Devuelve false si no se pudo dejar constancia:
     * en ese caso NO se debe hacer el POST (sin diario no hay forma de saber
     * despues si la factura salio, y reintentar duplicaria).
     */
    markSending(headerId, orderUuid) {
        const datos = leer();
        datos[headerId] = { estado: "enviando", orderUuid, ts: Date.now() };
        return escribir(datos);
    },

    /** El agente respondio con document_uuid: ya existe el documento fiscal. */
    markSent(headerId, documentUuid) {
        const datos = leer();
        const e = datos[headerId] || { ts: Date.now() };
        datos[headerId] = { ...e, estado: "enviado", documentUuid, ts: Date.now() };
        escribir(datos);
    },

    /** El servidor registro el resultado: la orden deja de estar en el aire. */
    clear(headerId) {
        const datos = leer();
        delete datos[headerId];
        escribir(datos);
    },

    /** Entradas sin resolver, para reconciliar al arrancar el POS. */
    pending() {
        const datos = leer();
        return Object.entries(datos)
            .filter(([, e]) => e && (e.estado === "enviando" || e.estado === "enviado"))
            .map(([headerId, e]) => ({ headerId, ...e }));
    },
};
