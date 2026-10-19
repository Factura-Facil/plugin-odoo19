# Changelog — Factura Electrónica Panamá (Factura Fácil) - POS

Formato: cada versión lista lo que cambia para quien instala o actualiza el módulo.

## 18.0.8.1.0

> ⚠️ Sin probar en Odoo 18.

### Corregido (portado de la 19.0)
- **Las facturas generadas por el POS aparecían sin estado FE.** El documento
  fiscal cuelga de la orden, no de la factura. Ahora la factura comparte el mismo
  registro `ff.fe.document` que su orden —no se copia nada— y se enlaza en ambos
  sentidos. Muestran el estado pero no ofrecen acciones FE: el documento es de la
  orden.



> ⚠️ Sin probar en Odoo 18. En la rama `19.0` todo esto está probado en
> producción y pasa 140 tests.

### Corregido (portado de la 19.0)
- **La pantalla en blanco del POS: un `not` en una plantilla Owl.** Owl solo
  traduce `and`, `or`, `gt`, `gte`, `lt` y `lte` (`WORD_REPLACEMENT` en
  `owl.js`). `not` se compilaba como identificador y la plantilla **entera** de
  `ReceiptScreen` dejaba de compilar. Va con un test que recorre las plantillas
  y falla si aparece un operador que Owl no traduce.
- **Validar el RUC usa la conexión de cada origen**: la caja que pregunta, luego
  la configuración de facturas de la compañía, luego cualquier POS alcanzable
  desde el servidor. Antes solo miraba los puntos de venta, así que desde
  Contabilidad con la API en línea decía "no hay agente".

### Nuevo
- **El estado de la FE siempre visible en la cabecera de la factura**, con la
  acción que toca en cada estado, y el motivo del fallo encima de la factura.
  El envío incierto se queda sin botón a propósito: reenviar duplicaría.
- Instrumentación: el error real de Owl en consola (no `[object Object]`), con
  componente y pila, y avisos que dicen qué pasó en el propio texto.

### Adaptaciones propias de esta rama
Lo que **no** se copia de la 19 y hay que mantener a mano:
- `init_lna` no existe en 18: el helper de `targetAddressSpace` se replica.
- **Las notas de crédito invierten el signo aquí**, porque el mixin de esta rama
  conserva la inversión por documento (en 18 el `price_subtotal` de una
  devolución sí viene en negativo).
- Sin la herencia de la vista de lista de facturas: su anclaje es de la 19.
- Los tests usan `_load_pos_data`, no `_load_pos_data_read`.



> ⚠️ Sin probar en Odoo 18. En la rama `19.0` está probado en una caja real.

### Corregido (portado de la 19.0)
- **La factura seguía descargándose sola** cuando la orden generaba factura. Era
  un problema de orden: el gancho de la descarga corre **antes** de la emisión,
  así que `fe_impreso` todavía es `false` ahí — la condición no podía cumplirse.
  Se suprime ahora por configuración, y la red de seguridad va detrás: si el
  agente no llegó a imprimir, la factura se descarga al terminar la emisión.
- El botón **"Ver factura de Odoo (PDF)"** aparece siempre que haya factura.



> ⚠️ Sin probar en Odoo 18, como el resto del modo. En la rama `19.0` todo esto
> está probado contra un agente real en una caja y pasa 130 tests.

### Nuevo
- **Sin doble paso para el cajero** cuando el agente imprime el CAFE: ni salta el
  recibo de Odoo ni se descarga sola la factura en PDF. Quedan a mano los botones
  **"Imprimir recibo (Odoo)"**, **"Reimprimir FE"** y **"Ver factura de Odoo
  (PDF)"**. Se decide por `fe_impreso`, no por la configuración: si el agente
  falló al imprimir, ambas cosas vuelven a salir — algo tiene que llevarse el
  cliente.

### Corregido (portado de la 19.0)
- Dos comprobaciones previas impedían emitir con el agente accesible: se exigía
  permiso `granted` y HTTP 200 en `/api/healthcheck/`. Ahora informan pero no
  bloquean.
- El cajero no podía preparar la emisión: las credenciales se leían como el
  usuario actual y llevan `groups=responsable del POS`. Se leen con `sudo()`.
- Dos llamadas RPC apuntaban a métodos con guion bajo, que Odoo bloquea
  remotamente. Expuestas como `ff_fe_data` y `ff_local_print_done`.
- La consola muestra el error real del servidor con su traceback, hay traza al
  entrar al gancho de pago, y `ffFeEmitir()` emite la orden actual sin hacer una
  venta entera.

### Adaptación específica de esta rama
- **Los dos ganchos de supresión** (`shouldDownloadInvoice` y `canPrintReceipt`)
  se enganchan en `PaymentScreen`, no en `OrderPaymentValidation`, que no existe
  en 18. **No se ha podido verificar que `PaymentScreen` los exponga en Odoo
  18**: si el recibo o la factura siguen saliendo solos con el agente
  imprimiendo, ese es el primer sitio donde mirar.
- El helper de `targetAddressSpace` se replica dentro del módulo, porque
  `init_lna` no existe en 18.



> ⚠️ Sin probar en Odoo 18, igual que la versión anterior.

### Nuevo
- **La impresión del CAFE también la manda la caja.** En modo "Agente en la caja"
  la impresora cuelga de esa máquina, así que el servidor no la alcanza: el POS
  llama a `printByCufe` contra su propio agente tras la firma. Son dos llamadas
  independientes — si la impresión falla, el documento fiscal sigue siendo
  válido y solo queda constancia en la auditoría.



> ⚠️ **Sin probar en Odoo 18.** Portado desde la rama `19.0`, donde SÍ está
> probado contra un agente real y pasa 120 tests. Aquí se adaptó a mano lo que
> difiere entre versiones (ver abajo), pero NO se ha ejecutado contra un Odoo 18.

### Nuevo
- **Modo "Agente en la caja"**: el Agente Offline está instalado en cada caja y lo
  llama el **navegador** (127.0.0.1), no el servidor. El servidor construye el
  payload y escribe el estado fiscal; el navegador solo transporta; un diario en
  `localStorage` impide que un documento salga dos veces — se escribe antes del
  POST y solo se borra cuando Odoo confirma. Al abrir el POS se reconcilia lo
  pendiente, sin reenviar nunca.
- **Traza siempre activa** en la consola (`ffFeLog()`), sin credenciales, y
  **diagnóstico** que distingue agente apagado, CORS y permiso de red local
  (`ffFeDiag()`).
- En modo local, la validación de RUC también la hace el navegador; el servidor
  solo interpreta la respuesta (`ff_ruc_from_agent`).

### Corregido
- `_ff_get_agent_config` cogía el primer punto de venta configurado sin mirar si
  el servidor podía alcanzarlo: con una caja en modo local, Odoo intentaba llamar
  a `127.0.0.1`, que desde el servidor es él mismo.

### Adaptaciones respecto a la rama 19.0
Donde el port no es una copia — y por tanto donde mirar primero si algo falla:
- **`init_lna` no existe en Odoo 18.** Importarlo rompería el bundle entero del
  POS, así que el helper de `targetAddressSpace` se replica dentro del módulo.
- **El enganche de emisión es `PaymentScreen._postPushOrderResolve`**, no
  `OrderPaymentValidation.beforePostPushOrderResolve`.
- **La carga del POS es `_load_pos_data(data)`**, no `_load_pos_data_read`.
- **El service worker**: el bypass se mantiene, pero su lista de exclusiones no se
  ha verificado contra el de Odoo 18. Si los GET fallan con permiso denegado, ahí
  está el primer sitio donde mirar.



> ⚠️ **Sin probar en Odoo 18.** Este port se escribió y se verificó contra Odoo
> 19 (rama `19.0`, donde pasa 98 tests). Aquí se revirtió a mano lo que era
> propio de la 19 y se comprobó por comparación que la lógica fiscal del POS
> queda idéntica a la de esta rama, pero NO se ha ejecutado contra un Odoo 18.
> Pruébalo en un entorno 18 antes de usarlo en producción.

### Nuevo
- **Facturas de Contabilidad ante la DGI.** Las facturas de cliente y sus notas
  de crédito emitidas fuera del POS se timbran con Factura Fácil, con el mismo
  ciclo que el POS (estado, CUFE, reintentos, auditoría, CAFE oficial). Se
  emiten solas al confirmar la factura, o a mano con el botón **Firmar / Emitir
  FE** de la cabecera. La nota de crédito viaja como NTC referida al CUFE de la
  factura original.
- **Configuración propia de la compañía** (Ajustes → Contabilidad → Facturas de
  cliente): URL, credenciales, ambiente y punto de facturación. OJO: el código
  de punto de facturación debe ser DISTINTO al de las cajas — Factura Fácil
  numera por punto de venta y compartirlo choca los correlativos ante la DGI.
- **Forma de pago DGI por diario** (`account.journal.ff_payment_type`), como ya
  existía por método de pago del POS. Lo que quede sin cobrar en la factura se
  informa como crédito (01).
- No hay doble emisión: una factura nacida de una orden POS no se vuelve a
  emitir aquí (el pedido ya la emitió con sus pagos reales y el agente no
  deduplica).

### Interno
- La aritmética fiscal (bases, ITBMS, reparto de descuentos de signo contrario,
  cuotas de crédito) vive ahora en el mixin `ff.fe.source`, que comparten
  `pos.order` y `account.move`: un arreglo sirve para los dos. Las fuentes solo
  aportan de dónde salen los datos.

### Diferencias con la rama 19.0
Los arreglos de la 19.0.2.0.0 **no** se traen aquí porque corrigen cosas que
solo están rotas en Odoo 19: el signo de las líneas de devolución (en 18 el
`price_subtotal` ya viene en negativo, que es lo que esta rama espera),
`models.Constraint` (no existe en 18, aquí sigue `_sql_constraints`), y las
vistas de `uom.uom` y de la búsqueda de órdenes, válidas tal cual en 18.
Tampoco se hereda la vista de lista de facturas: su anclaje es de la 19.

## 18.0.3.1.0

### Nuevo
- **Botón “Firmar / Emitir FE” en el backend**, en la cabecera de la orden POS.
  Cierra el ciclo: permite firmar una orden que aún no tiene documento — creada
  en el backend, o del POS que nunca llegó a emitir. Crea el documento si falta
  y lo emite en el momento (mismo flujo que el POS). Aparece solo cuando la
  orden está cobrada y el punto de venta tiene Factura Fácil activo; una vez con
  documento, se usa “Reintentar / actualizar FE”.

## 18.0.3.0.0 — 2026-09-24

### Nuevo
- **Modo de emisión** por punto de venta: *Agente offline* (como hasta ahora) o **API en línea**
  de Factura Fácil. Mismo documento; en línea la factura se autoriza al momento.
- En línea, el **PDF oficial del CAFE** (`download_cafe`) queda adjunto a la orden, con el botón
  *Ver CAFE oficial* en el POS y en el backend. El recibo sigue saliendo con el diseño CAFE de Odoo.
- Validación de RUC con la DGI también en línea.

### Corregido
- Un cajero no podía validar un RUC desde el POS (error de acceso desde 18.0.2.0.0, al restringir
  la API Key a responsables).

### A tener en cuenta
- Los dos modos **comparten la numeración** del punto de venta en Factura Fácil. No uses el mismo
  código POS en una caja con agente y otra en línea: si el agente numera sin conexión, los números
  pueden chocar.

## 18.0.2.1.0 — 2026-09-23

### Nuevo
- **Forma de pago DGI** por método de pago del POS (`Crédito`, `Efectivo`, tarjetas, `Vale`,
  `Tarjeta de regalo`, `Transferencia`, `Cheque`, `Otro`…). Se propone al instalar a partir del
  tipo del método y se puede corregir con las cajas abiertas.
- **Código DGI de la unidad de medida** (vacío = `und`).
- **Ventas a crédito** (cuenta de cliente) informadas como forma de pago *Crédito*. Opción
  *Informar cuotas de crédito* para enviar plazo y cuota cuando el agente lo soporte.
- Botón **Probar conexión** en los ajustes del POS.
- Ficha de la tienda de apps (ícono, banner, capturas).
- Plantilla de traducción `i18n/ff_pos_agent.pot`.

### Cambios
- Antes, la cuenta de cliente y cualquier método con un nombre no reconocido se informaban como
  *Efectivo*. Ahora la cuenta de cliente es *Crédito* y lo desconocido, *Otro* con descripción.
  **Revisá la forma de pago DGI de tus métodos al actualizar.**
- Un impuesto que no es una tasa de ITBMS (7, 10 o 15%) deja la orden en *Error* con el motivo,
  en vez de enviarse como exento.
- Todos los textos de la interfaz, los mensajes y el CAFE, con la ortografía correcta
  ("Factura Electrónica", "Código", "Envío incierto"…).

### Problemas conocidos del agente
- El agente falla con cuotas de crédito (HTTP 500). Ver *Bugs conocidos del agente* en
  `INTEGRACION_ODOO_fe_detailed.md`.

## 18.0.2.0.0 — 2026-09-23

Actualización grande. Incluye una migración: hacé un respaldo de la base antes de actualizar.

### Nuevo
- Modelo **`ff.fe.document`**: el documento fiscal de cada venta, con su estado, CUFE, auditoría
  y reintentos. Las columnas FE de la orden pasan a ser un espejo del documento.
- Estado **Envío incierto**: si el agente pudo haber recibido la venta sin confirmarla (timeout,
  corte, HTTP 5xx), no se reenvía sola. Un responsable del POS la resuelve vinculando el UUID del
  documento o con *Forzar reenvío*.
- El cron reintenta las ventas pendientes y deja de insistir con un agente caído.

### Cambios
- **La emisión va aparte de la venta**: la sincronización de la orden ya no habla con el agente;
  el POS pide la emisión justo después y el recibo sale igual con el CAFE. Si el navegador se
  cierra, el cron la emite a los 2 minutos.
- Antes de enviar se consulta el healthcheck del agente: con el agente apagado la caja espera
  ~3 s (antes hasta 45 s).
- **Solo se facturan las ventas cobradas.** Los borradores (mesas abiertas) nunca se emiten.
  Las devoluciones pagadas desde el backend ahora sí se emiten.
- Precios con **ITBMS incluido** y **descuentos globales / premios** se emiten con el total
  cobrado (antes se facturaba de más y el agente lo rechazaba).
- **Seguridad**: la API Key y el Company UUID solo los ve un responsable del POS y ya no se cargan
  en el navegador de la caja; el estado fiscal es de solo lectura; la auditoría enmascara la
  API Key. **Si usaste la 18.0.1.0.0 en una caja real, rotá la API Key.**
- La auditoría conserva el historial de todos los intentos.

### Migración
- Se crea un documento FE por cada orden con actividad FE (payload enviado, CUFE o UUID). Las
  demás no reciben documento y **no se emiten**.

### Corregido
- En la 18.0.1.0.0 toda orden nacía *pendiente*: con el reintento automático del cron, una base
  con historial habría emitido sus ventas antiguas. Corregido: solo se emite lo que tiene
  documento, y el documento nace al cobrar.

## 18.0.1.0.0 — 2026-09-22

Primera versión: emisión desde el POS, CAFE en el recibo, notas de crédito, validación de RUC con
la DGI, facturas a Gobierno (GNS), impresión en el agente, Monitor y auditoría.
