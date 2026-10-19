/** @odoo-module **/
/**
 * Por que no se pudo hablar con el agente de la caja.
 *
 * Un `fetch` fallido llega al JS como un TypeError seco, igual si el agente
 * esta apagado que si el navegador bloqueo la respuesta por CORS. Para el
 * cajero (y para quien lea el log despues) eso no sirve de nada.
 *
 * El truco para distinguirlos es `mode: "no-cors"`: esa peticion SI sale y
 * vuelve, aunque opaca, cuando el servidor esta ahi — porque no exige
 * cabeceras CORS. Entonces:
 *
 *   no-cors OK  + normal falla  -> el agente responde, lo bloquea el CORS
 *   no-cors falla               -> no se llega al agente (apagado, puerto,
 *                                  o permiso de red local denegado)
 */
import { getLNATargetAddressSpace } from "@point_of_sale/app/utils/init_lna";
import { sinServiceWorker } from "./ff_agent";
import { ffLog } from "./ff_log";

const TIMEOUT = 4000;

async function sonda(url, opciones) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), TIMEOUT);
    try {
        await fetch(url, {
            ...opciones,
            signal: ctrl.signal,
            targetAddressSpace: getLNATargetAddressSpace(url),
        });
        return { ok: true };
    } catch (e) {
        return { ok: false, error: e?.name === "AbortError" ? "timeout" : e?.message || String(e) };
    } finally {
        clearTimeout(t);
    }
}

/** Estado del permiso de red local de Chrome (LNA). */
async function permisoLNA() {
    try {
        const r = await navigator.permissions.query({ name: "local-network-access" });
        return r.state; // granted | prompt | denied
    } catch {
        return "no-soportado";
    }
}

/**
 * Diagnostica la conexion con el agente y devuelve un veredicto legible.
 * Se ejecuta solo cuando algo falla, y tambien a mano: ffFeDiag().
 */
export async function ffDiagnosticar(baseUrl) {
    const base = (baseUrl || "").replace(/\/+$/, "");
    const url = sinServiceWorker(base + "/api/healthcheck/");

    const permiso = await permisoLNA();
    // Sin permiso concedido no tiene sentido sondear: Chrome rechaza todo por
    // igual y el resultado no distingue nada. Se informa y se corta.
    if (permiso !== "granted") {
        const informe = {
            base,
            permiso_red_local: permiso,
            causa:
                permiso === "denied"
                    ? "Chrome tiene BLOQUEADO el acceso a la red local para este sitio."
                    : permiso === "no-soportado"
                      ? "Este navegador no soporta Local Network Access."
                      : "Falta conceder el acceso a la red local (nadie aceptó el aviso).",
            arreglo:
                permiso === "no-soportado"
                    ? "Usa Chrome, Edge o Brave 142 o superior. Firefox e iOS no sirven."
                    : "Pulsa 'Autorizar el agente' en el aviso del POS, o ábrelo en el " +
                      "candado de la barra de direcciones → Configuración del sitio → " +
                      "Acceso a la red local → Permitir.",
        };
        ffLog("DIAGNÓSTICO", informe);
        console.warn(`%c[FE Caja] ${informe.causa}\n→ ${informe.arreglo}`, "color:#b34700;font-weight:bold");
        return informe;
    }
    const normal = await sonda(url, { method: "GET" });
    const opaca = normal.ok ? { ok: true } : await sonda(url, { method: "GET", mode: "no-cors" });

    let causa;
    let arreglo;

    if (normal.ok) {
        causa = "OK — el agente responde y el CORS está bien configurado.";
        arreglo = null;
    } else if (opaca.ok) {
        causa =
            "El agente responde, pero el navegador descarta la respuesta: le faltan " +
            "cabeceras CORS para este dominio.";
        arreglo =
            "El agente debe devolver Access-Control-Allow-Origin con este dominio (o *), " +
            "Access-Control-Allow-Headers con x-ff-company y x-ff-api-key, y " +
            "Access-Control-Allow-Private-Network: true. Es configuración del agente, " +
            "no de Odoo. Comprueba también que reiniciaste el agente tras el cambio.";
    } else if (permiso === "denied") {
        causa = "Chrome tiene DENEGADO el permiso de red local para este sitio.";
        arreglo =
            "Candado de la barra de direcciones → Configuración del sitio → " +
            "Acceso a la red local → Permitir. Luego recarga el POS.";
    } else if (permiso === "prompt") {
        causa = "Chrome aún no ha concedido el permiso de red local (nadie lo aceptó).";
        arreglo = "Recarga el POS y acepta el aviso de acceso a la red local cuando aparezca.";
    } else if (permiso === "no-soportado") {
        causa =
            "Este navegador no soporta Local Network Access: no puede llamar a " +
            "127.0.0.1 desde una web pública.";
        arreglo =
            "Usa Chrome, Edge o Brave 142 o superior. Firefox e iOS no sirven. " +
            "Y activa el parámetro de sistema point_of_sale.use_lna = 1 en Odoo.";
    } else {
        causa = `No se llega al agente en ${base} (${normal.error}).`;
        arreglo =
            "Comprueba que el Agente Offline está arrancado en esa caja y escuchando " +
            "en ese puerto: netstat -ano | findstr :8080";
    }

    const informe = { base, permiso_red_local: permiso, respuesta_normal: normal, respuesta_opaca: opaca, causa, arreglo };
    ffLog("DIAGNÓSTICO", informe);
    if (arreglo) {
        console.warn(`%c[FE Caja] ${causa}\n→ ${arreglo}`, "color:#b34700;font-weight:bold");
    }
    return informe;
}

// Para lanzarlo a mano desde la consola de la caja.
if (typeof window !== "undefined") {
    window.ffFeDiag = (base) => ffDiagnosticar(base || window.__ffBaseUrl || "http://127.0.0.1:8080");
}
