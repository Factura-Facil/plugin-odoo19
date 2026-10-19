/** @odoo-module **/
/**
 * Cliente HTTP del Agente Offline de Factura Facil, desde el navegador de la
 * caja (modo "agente en la caja").
 *
 * Espeja el flujo del servidor (ver ff_fe_document._ff_sign), con la misma
 * regla de oro: distinguir lo que SEGURO no llego de lo que pudo llegar.
 * Un envio incierto jamas se reintenta solo.
 *
 * Red: el agente escucha en 127.0.0.1, que para Chrome es "loopback". Una
 * pagina HTTPS publica necesita Local Network Access (Chrome/Edge 142+) y que
 * el agente permita el origen de Odoo por CORS.
 */
import { ffLog, ffWarn } from "./ff_log";

/**
 * Espacio de direcciones para Local Network Access.
 *
 * En Odoo 19 esto lo da `@point_of_sale/app/utils/init_lna`, que no existe en
 * 18: importarlo rompería el bundle entero del POS. Se replica aquí, que son
 * cuatro líneas — `targetAddressSpace` lo interpreta el navegador (Chrome 142+),
 * no Odoo, así que se comporta igual en las dos versiones.
 */
function getLNATargetAddressSpace(url) {
    let hostname;
    try {
        hostname = new URL(url).hostname;
    } catch {
        hostname = url;
    }
    return hostname === "localhost" || hostname === "127.0.0.1" ? "loopback" : "local";
}

const EP_SIGN = "/api/pac/reception_fe/detailed/";
const EP_HEALTH = "/api/healthcheck/";
// La impresion del CAFE es su propia ruta, con la api_key EN LA URL: el body de
// reception_fe solo acepta {header, document} y descarta el resto en silencio.
const EP_PRINT = (apiKey) => `/fe/rest/api/printByCufe/${apiKey}`;
const EP_DOC = (uuid) => `/api/document/${uuid}/`;
const EP_RESULT = (env, company, cufe) => `/api/fepa/v1/${env}/GetResultFe/${company}/${cufe}`;

const TIMEOUT_HEALTH = 3000;
const TIMEOUT_POST = 45000;
const TIMEOUT_POLL = 10000;
const POLL_INTENTOS = 6;
const POLL_ESPERA = 700;

const espera = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Esquiva el service worker del POS.
 *
 * `point_of_sale/static/src/app/service_worker.js` intercepta TODO GET salvo
 * unas pocas excepciones, y lo reemite con `fetch(event.request)` desde su
 * propio contexto. En esa reemision se pierden dos cosas imprescindibles aqui:
 * `targetAddressSpace` (es una opcion de fetch, no viaja en el Request) y la
 * activacion del usuario. Sin ellas Chrome deniega el acceso a la red local
 * sin ni siquiera preguntar.
 *
 * Odoo tiene el mismo problema con su hardware local y lo resuelve excluyendo
 * a mano esas URLs (`hw_proxy/hello`, `Cashdro3WS/index3.php`). Como no podemos
 * tocar su service worker, se añade un parametro inocuo que cae en una de sus
 * exclusiones: asi la peticion llega intacta al navegador.
 *
 * El POST de emision no lo necesita: el service worker ignora todo lo que no
 * sea GET.
 */
const SW_BYPASS = "ff_sw=web/dataset";

function sinServiceWorker(url) {
    return url + (url.includes("?") ? "&" : "?") + SW_BYPASS;
}

/** fetch con timeout y con el permiso de red local que exige Chrome. */
async function pedir(url, opciones = {}, timeout = TIMEOUT_POST) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    const t0 = performance.now();
    const metodo = opciones.method || "GET";
    const espacio = getLNATargetAddressSpace(url);
    ffLog(`→ ${metodo} ${url}`, { targetAddressSpace: espacio, timeout_ms: timeout });
    try {
        const r = await fetch(url, {
            ...opciones,
            signal: ctrl.signal,
            targetAddressSpace: espacio,
        });
        ffLog(`← ${r.status} ${metodo} ${url}`, { ms: Math.round(performance.now() - t0) });
        return r;
    } catch (e) {
        // Aqui llegan por igual: agente apagado, CORS y permiso de red local.
        // El diagnostico los distingue (ver ff_diag.js).
        ffWarn(`✗ ${metodo} ${url}`, {
            ms: Math.round(performance.now() - t0),
            error: e?.name === "AbortError" ? "timeout" : e?.message || String(e),
        });
        throw e;
    } finally {
        clearTimeout(t);
    }
}

export { sinServiceWorker };

export class FfLocalAgent {
    constructor({ base_url, env_name, company_uuid, api_key }) {
        this.apiKey = api_key || "";
        this.base = (base_url || "").replace(/\/+$/, "");
        if (typeof window !== "undefined") {
            window.__ffBaseUrl = this.base; // lo usa ffFeDiag() sin argumentos
        }
        this.env = env_name || "test";
        this.company = company_uuid || "";
        this.headers = {
            "Content-Type": "application/json",
            "X-FF-Company": company_uuid || "",
            "X-FF-API-Key": api_key || "",
        };
    }

    /**
     * (true, "") si el agente esta ahi.
     *
     * Basta con que RESPONDA algo, aunque sea 404: eso ya prueba que hay un
     * servidor escuchando y que el navegador puede hablarle. Exigir un 200 era
     * un falso negativo en cuanto el agente no expone /api/healthcheck/, y
     * dejaba la venta sin emitir sin que nadie lo hubiera pedido.
     *
     * Solo un fallo de red (conexion rechazada, DNS, CORS, permiso denegado,
     * timeout) significa que el POST no llegaria.
     */
    async vivo() {
        try {
            const r = await pedir(
                sinServiceWorker(this.base + EP_HEALTH),
                { method: "GET", headers: { "X-FF-Company": this.company } },
                TIMEOUT_HEALTH
            );
            if (r.status !== 200) {
                ffLog("Healthcheck sin 200, pero el agente responde", { status: r.status });
            }
            return [true, ""];
        } catch (e) {
            // Un fallo de CORS llega aqui como TypeError, indistinguible de
            // "no hay nadie escuchando". En ambos casos el POST no sale.
            return [false, e?.name === "AbortError" ? "timeout" : e?.message || "sin respuesta"];
        }
    }

    /**
     * Emite el documento. Devuelve siempre un objeto con `estado`, que el
     * servidor traduce a estado fiscal:
     *   no_entregado  la peticion no salio -> se puede reintentar
     *   incierta      pudo llegar -> NO se reintenta solo
     *   error         el agente rechazo (4xx) -> no creo nada
     *   ok            hay document_uuid
     */
    async emitir(payload) {
        let resp;
        try {
            resp = await pedir(this.base + EP_SIGN, {
                method: "POST",
                headers: this.headers,
                body: JSON.stringify(payload),
            });
        } catch (e) {
            if (e?.name === "AbortError") {
                // Timeout de lectura: el agente pudo haberlo procesado.
                return { estado: "incierta", message: "El agente no respondió a tiempo." };
            }
            // TypeError = conexion rechazada, DNS o CORS: no llego a procesarse.
            return {
                estado: "no_entregado",
                message: `No se pudo contactar el agente local (${e?.message || e}).`,
            };
        }

        const texto = await resp.text().catch(() => "");
        let datos = {};
        try {
            datos = JSON.parse(texto || "{}");
        } catch {
            datos = {};
        }
        const comun = { status: resp.status, body: texto.slice(0, 4000), payload };

        if (resp.status >= 500) {
            // Pudo crear el documento antes de fallar.
            return { ...comun, estado: "incierta", message: `HTTP ${resp.status}` };
        }
        if (resp.status !== 200 && resp.status !== 201) {
            return { ...comun, estado: "error", message: this.#errorLegible(datos, texto, resp.status) };
        }

        // Si el agente avisa de duplicado, el documento valido es el original.
        const original = datos.duplicated ? datos.original : null;
        const uuid = (original || {}).document_uuid || datos.document_uuid;
        if (!uuid) {
            return { ...comun, estado: "incierta", message: `HTTP ${resp.status} sin document_uuid` };
        }
        return { ...comun, estado: "ok", document_uuid: uuid };
    }

    /** El rFE firmado (con el CUFE). Puede tardar: el agente firma en diferido. */
    async firma(uuid) {
        for (let i = 0; i < POLL_INTENTOS; i++) {
            try {
                const r = await pedir(
                    sinServiceWorker(this.base + EP_DOC(uuid)),
                    { method: "GET", headers: this.headers },
                    TIMEOUT_POLL
                );
                if (r.status === 200) {
                    const d = await r.json();
                    const rfe = d?.xml_data?.rFE;
                    if (rfe) {
                        return rfe;
                    }
                }
            } catch {
                // se reintenta
            }
            await espera(POLL_ESPERA);
        }
        return null;
    }

    /**
     * Manda imprimir el CAFE en la impresora del agente de esta caja.
     *
     * Nunca rompe la venta: si falla, se devuelve false y queda en la
     * auditoria. Es POST, asi que el service worker del POS no lo intercepta.
     */
    async imprimir(cufe) {
        if (!cufe) {
            return { impreso: false, message: "sin CUFE" };
        }
        try {
            const r = await pedir(this.base + EP_PRINT(this.apiKey), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ cufe }),
            });
            const texto = await r.text().catch(() => "");
            return {
                impreso: r.status === 200,
                status: r.status,
                body: texto.slice(0, 1000),
            };
        } catch (e) {
            return { impreso: false, message: e?.message || String(e) };
        }
    }

    /** Resultado del PAC (autorizada / rechazada). Null si aun no hay. */
    async resultado(cufe) {
        try {
            const r = await pedir(
                sinServiceWorker(this.base + EP_RESULT(this.env, this.company, cufe)),
                { method: "GET", headers: this.headers },
                TIMEOUT_POLL
            );
            return r.status === 200 ? await r.json() : null;
        } catch {
            return null;
        }
    }

    #errorLegible(datos, texto, status) {
        const partes = [];
        const recorrer = (v, prefijo = "") => {
            if (typeof v === "string") {
                partes.push(prefijo ? `${prefijo}: ${v}` : v);
            } else if (Array.isArray(v)) {
                v.forEach((x) => recorrer(x, prefijo));
            } else if (v && typeof v === "object") {
                Object.entries(v).forEach(([k, x]) => recorrer(x, prefijo ? `${prefijo}.${k}` : k));
            }
        };
        recorrer(datos);
        return partes.length ? partes.join(" | ").slice(0, 1000) : `HTTP ${status}: ${texto.slice(0, 300)}`;
    }
}
