/** @odoo-module **/
/**
 * Emision FE desde la caja (modo "agente en la caja").
 *
 * Reparto de responsabilidades, a proposito:
 *   - el SERVIDOR construye el payload (codigo probado) y escribe el estado
 *     fiscal, validando lo que la caja le devuelve;
 *   - el NAVEGADOR solo transporta, porque el agente esta en 127.0.0.1 de esa
 *     caja y Odoo no lo alcanza;
 *   - el DIARIO (ff_journal) impide que un mismo documento salga dos veces.
 *
 * Limitacion conocida de esta etapa: el payload lo prepara el servidor, asi
 * que durante un corte de internet la venta se cobra y se encola, pero el
 * timbrado se hace al reconectar (lo reintenta `reconciliar`). Emitir con la
 * caja completamente aislada exige portar la aritmetica fiscal al navegador,
 * que es el siguiente paso y tiene su propio riesgo.
 */
import { FfLocalAgent } from "./ff_agent";
import { ffDiagnosticar } from "./ff_diag";
import { ffJournal } from "./ff_journal";
import { ffError, ffLog, ffWarn } from "./ff_log";
import { ffAsegurarPermiso, ffEstadoPermiso } from "./ff_permiso";

/**
 * Emite una orden contra el agente local y deja el resultado en el servidor.
 * Nunca lanza: devuelve los campos FE para el recibo, o null si no aplica.
 */
export async function ffEmitirEnCaja(pos, order) {
    if (!order?.id) {
        return null;
    }
    ffLog("=== EMISIÓN FE DESDE LA CAJA ===", { orden: order.name, id: order.id });
    // 1. Lo que el servidor sabe: payload, credenciales y si procede emitir.
    let prep;
    try {
        prep = await pos.data.call("pos.order", "ff_local_prepare", [[order.id]]);
    } catch (e) {
        // `e.message` suele ser solo "Odoo Server Error": lo util esta en
        // e.data (message del UserError, y el traceback en debug).
        const datos = e?.data || {};
        ffWarn("1. Preparar en el servidor: FALLÓ", {
            ...ffError(e),
            causa: datos.message
                ? "Odoo respondió con un error al preparar el documento."
                : "Odoo no respondió. Sin internet no se puede preparar el documento.",
        });
        return null;
    }
    if (prep?.skip) {
        ffLog("1. Preparar en el servidor: NO PROCEDE", {
            motivo: prep.skip,
            explicación: {
                ya_emitido: "Ya existe en el agente. No se reemite nunca: duplicaría la factura.",
                incierta: "Un envío anterior quedó sin respuesta. Hay que resolverlo en el backend.",
                no_emitible: "La orden no está cobrada o el POS no tiene Factura Fácil activo.",
                error: prep.message || "",
            }[prep.skip],
        });
        // Ya emitido, incierto o no emitible: el servidor ya dejo el estado.
        return await ffLeerEstado(pos, order);
    }

    const headerId = prep.header_id;
    ffLog("1. Preparado por el servidor", {
        header_id: headerId,
        agente: prep.base_url,
        items: prep.payload?.document?.items?.length,
        tipo: prep.payload?.document?.type,
    });
    // 2. Un envio anterior sin resolver bloquea: reenviar duplicaria la factura.
    const previo = ffJournal.get(headerId);
    if (previo) {
        ffWarn("2. BLOQUEADO por un envío anterior sin confirmar", {
            estado: previo.estado,
            document_uuid: previo.documentUuid,
            causa: "Esta caja ya envió este documento y no se confirmó el resultado.",
            arreglo: "Resuélvelo en el backend (orden POS → Factura Electrónica). No se reenvía para no duplicar.",
        });
        return await ffReportar(pos, order, headerId, {
            estado: previo.estado === "enviado" ? "ok" : "incierta",
            document_uuid: previo.documentUuid,
            message:
                "Quedó un envío anterior sin confirmar en esta caja. No se reenvía " +
                "para no duplicar la factura ante la DGI; revísalo en el backend.",
        });
    }

    // 2.bis El permiso se informa, pero NO bloquea.
    //
    // `navigator.permissions.query({name:"local-network-access"})` puede
    // devolver "prompt" aunque las peticiones ya funcionen (el estado no
    // siempre refleja una concesion implicita, y el nombre del permiso no
    // existe en todas las versiones). Cortar aqui dejaba ventas sin emitir con
    // el agente perfectamente accesible. Quien decide es la peticion real.
    const permiso = await ffEstadoPermiso();
    ffLog("2. Permiso de red local", { permiso, nota: "informativo: no bloquea el envío" });

    const agente = new FfLocalAgent(prep);

    // 3. Sondeo previo: si el agente no contesta, el POST ni se intenta y la
    //    orden queda pendiente (reenvio seguro). Es lo que hace el servidor.
    const [vivo, detalle] = await agente.vivo();
    if (!vivo) {
        ffWarn("3. El agente no responde", { detalle });
        // Distingue agente apagado / CORS / permiso de red local.
        await ffDiagnosticar(prep.base_url);
        if (permiso !== "granted") {
            // Ahora sí tiene sentido ofrecer el botón: falló de verdad.
            await ffAsegurarPermiso(pos);
        }
        return await ffReportar(pos, order, headerId, {
            estado: "no_entregado",
            message: `El agente local no responde (${detalle}). La factura no se envió.`,
        });
    }

    // 4. Marca ANTES de salir. Sin diario no se emite: no habria forma de
    //    saber despues si la factura salio.
    if (!ffJournal.markSending(headerId, order.uuid)) {
        return await ffReportar(pos, order, headerId, {
            estado: "no_entregado",
            message:
                "No se pudo registrar el envío en esta caja (almacenamiento del " +
                "navegador bloqueado). No se emite, para no arriesgar una factura duplicada.",
        });
    }

    ffLog("3. Agente vivo — 4. Envío registrado en el diario de esta caja", { header_id: headerId });

    // 5. Emision.
    const res = await agente.emitir(prep.payload);
    ffLog("5. Respuesta del agente", {
        estado: res.estado,
        http: res.status,
        document_uuid: res.document_uuid,
        mensaje: res.message,
    });
    if (res.estado !== "ok") {
        ffWarn("5. No se emitió", {
            estado: res.estado,
            explicación: {
                no_entregado: "La petición no salió. Reenviar es seguro.",
                incierta: "Pudo llegar al agente. NO se reenvía: duplicaría la factura ante la DGI.",
                error: "El agente rechazó el documento (4xx). No creó nada.",
            }[res.estado],
            mensaje: res.message,
        });
        if (res.estado === "no_entregado") {
            await ffDiagnosticar(prep.base_url);
        }
        if (res.estado === "no_entregado" || res.estado === "error") {
            // Seguro que no se creo nada: el diario se libera.
            ffJournal.clear(headerId);
        }
        return await ffReportar(pos, order, headerId, res, res.estado === "incierta");
    }
    ffJournal.markSent(headerId, res.document_uuid);

    // 6. Firma (CUFE) y, si la hay, el resultado del PAC.
    const rfe = await agente.firma(res.document_uuid);
    const cufe = rfe?.dId;
    if (!cufe) {
        ffWarn("6. Sin CUFE todavía", {
            causa: "El agente creó el documento pero aún no lo ha firmado.",
            arreglo: "No se pierde nada: pulsa Reintentar en el recibo, o se resuelve al reabrir el POS.",
        });
    } else {
        ffLog("6. Firmado", { cufe, nro_dgi: rfe?.gDGen?.dNroDF });
    }
    const resultado = cufe ? await agente.resultado(cufe) : null;
    if (resultado) {
        ffLog("7. Resultado del PAC", {
            autorizado: resultado.authorized,
            protocolo: resultado.authNumber,
            error_dgi: resultado.dgiErrMsg,
        });
    }

    // 8. Impresion del CAFE en la impresora del propio agente, si el punto de
    //    venta lo pide. Es una llamada aparte de la firma, y su fallo no afecta
    //    al documento fiscal: la venta ya está emitida.
    let impresion = null;
    if (prep.print_cafe && cufe) {
        impresion = await agente.imprimir(cufe);
        ffLog(impresion.impreso ? "8. CAFE impreso por el agente" : "8. No se pudo imprimir el CAFE", {
            http: impresion.status,
            mensaje: impresion.message,
            nota: impresion.impreso ? undefined : "El documento fiscal es válido igualmente.",
        });
    }

    return await ffReportar(pos, order, headerId, { ...res, rfe, resultado, impresion });
}

/** Entrega el resultado al servidor, que es quien escribe el estado fiscal. */
async function ffReportar(pos, order, headerId, resultado, conservarDiario = false) {
    try {
        const rec = await pos.data.call("pos.order", "ff_local_apply", [[order.id], resultado]);
        ffLog("8. Registrado en Odoo", { estado_fiscal: rec?.fe_estado, cufe: rec?.fe_cufe });
        if (!conservarDiario) {
            // El servidor ya tiene constancia: la orden deja de estar en el aire.
            ffJournal.clear(headerId);
        }
        // Con el documento ya emitido ante la DGI, pintar el recibo no puede
        // tumbar la caja: si algo falla aqui, se registra y se sigue.
        try {
            order.ffApplyFe(rec);
            ffLog("9. Datos FE aplicados a la orden", { estado: rec?.fe_estado });
        } catch (e) {
            ffWarn("9. No se pudieron aplicar los datos FE a la orden", ffError(e));
        }
        return rec;
    } catch (e) {
        // El documento puede existir ya en el agente: el diario NO se toca,
        // para que `reconciliar` lo reintente al volver la conexion.
        ffWarn("8. No se pudo registrar en Odoo", {
            ...ffError(e),
            causa: "El documento puede existir ya en el agente.",
            arreglo: "El diario NO se borra: al reabrir el POS se reconcilia solo.",
        });
        return null;
    }
}

async function ffLeerEstado(pos, order) {
    try {
        const rec = await pos.data.call("pos.order", "ff_fe_data", [[order.id]]);
        order.ffApplyFe(rec);
        return rec;
    } catch {
        return null;
    }
}

/**
 * Al arrancar el POS, cierra los envios que quedaron en el aire (caja apagada
 * a mitad, navegador cerrado, Odoo inalcanzable al reportar).
 *
 * Nunca reenvia: si hay UUID consulta la firma, y si no lo hay lo reporta como
 * envio incierto para que un responsable lo resuelva en el backend.
 */
export async function ffReconciliarCaja(pos) {
    const pendientes = ffJournal.pending();
    if (!pendientes.length || !pos.config.ff_agent_enabled) {
        return;
    }
    for (const entrada of pendientes) {
        const order = pos.models["pos.order"].find((o) => o.uuid === entrada.orderUuid);
        if (!order?.id) {
            continue; // aun sin sincronizar: se reintenta en el siguiente arranque
        }
        try {
            if (entrada.estado === "enviado" && entrada.documentUuid) {
                const prep = await pos.data.call("pos.order", "ff_local_prepare", [[order.id]]);
                const agente = new FfLocalAgent(prep.skip ? prep : prep);
                const rfe = await agente.firma(entrada.documentUuid);
                const resultado = rfe?.dId ? await agente.resultado(rfe.dId) : null;
                // No se reimprime al reconciliar: el cliente ya se fue y el
                // agente no deduplica impresiones. Se hace desde el recibo.
                await ffReportar(pos, order, entrada.headerId, {
                    estado: "ok",
                    document_uuid: entrada.documentUuid,
                    rfe,
                    resultado,
                });
            } else {
                await ffReportar(pos, order, entrada.headerId, {
                    estado: "incierta",
                    message:
                        "El envío al agente quedó sin confirmar (la caja se cerró " +
                        "durante la emisión). No se reenvía para no duplicar.",
                });
            }
        } catch (e) {
            console.warn("[ff_pos_agent] reconciliación pendiente:", entrada.headerId, e);
        }
    }
}

/**
 * Reimprime el CAFE en la impresora del agente de esta caja.
 *
 * Se usa desde el recibo cuando el agente no llego a imprimir, o cuando el
 * cliente pide otra copia. No toca el documento fiscal: ya esta emitido.
 */
export async function ffImprimirCafe(pos, order) {
    if (!order?.id || !order.fe_cufe) {
        return { impreso: false, message: "La orden no tiene CUFE todavía." };
    }
    let prep;
    try {
        prep = await pos.data.call("pos.order", "ff_local_prepare", [[order.id]]);
    } catch (e) {
        ffWarn("Reimpresión: no se pudo leer la configuración", {
            error: e?.data?.message || e?.message || String(e),
        });
        return { impreso: false, message: "No se pudo contactar Odoo." };
    }
    const agente = new FfLocalAgent(prep);
    const res = await agente.imprimir(order.fe_cufe);
    ffLog(res.impreso ? "Reimpresión del CAFE: OK" : "Reimpresión del CAFE: falló", {
        http: res.status,
        mensaje: res.message,
    });
    if (res.impreso) {
        try {
            await pos.data.call("pos.order", "ff_local_print_done", [[order.id], res]);
            order.fe_impreso = true;
        } catch {
            // La impresion salio; que Odoo no lo registre no es grave.
        }
    }
    return res;
}
